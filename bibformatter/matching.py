"""Deciding whether a database record is the same work as a local entry.

Title similarity is the gate; author overlap and year proximity break ties and
can veto a title-only coincidence. Getting this wrong in either direction is
costly, so the thresholds are configurable and every score is reported.
"""

from __future__ import annotations

from dataclasses import dataclass
from difflib import SequenceMatcher
from typing import List, Optional

from bibformatter.normalize import name_key, normalize_for_match
from bibformatter.providers import Record

# Words ignored when comparing titles as bags of words.
_STOPWORDS = {
    "a", "an", "the", "of", "for", "and", "or", "in", "on", "with", "to", "via",
    "using", "from", "by", "at", "as", "is", "are",
}


@dataclass
class MatchScore:
    title: float = 0.0
    authors: float = 0.0
    year: float = 0.0
    # How much of the shorter title is contained in the longer one. Separates
    # "citation dropped the subtitle" from "different paper".
    contains: float = 0.0
    overall: float = 0.0
    doi_exact: bool = False
    # Whether both sides actually listed authors. Without this, "no overlap" and
    # "nothing to compare" are indistinguishable, and only the former is evidence.
    authors_comparable: bool = False

    def as_dict(self) -> dict:
        return {
            "title": round(self.title, 3),
            "authors": round(self.authors, 3),
            "year": round(self.year, 3),
            "contains": round(self.contains, 3),
            "overall": round(self.overall, 3),
            "doi_exact": self.doi_exact,
        }


def token_containment(left: str, right: str) -> float:
    """Fraction of the shorter title's words that appear in the longer one."""
    left_tokens = {t for t in normalize_for_match(left).split() if t not in _STOPWORDS}
    right_tokens = {t for t in normalize_for_match(right).split() if t not in _STOPWORDS}
    if not left_tokens or not right_tokens:
        return 0.0
    return len(left_tokens & right_tokens) / min(len(left_tokens), len(right_tokens))


def title_similarity(left: str, right: str) -> float:
    """Blend sequence similarity with token overlap.

    Sequence ratio alone punishes subtitle differences ("BERT: Pre-training ..."
    vs "BERT"); token overlap alone accepts word-salad. Taking the max of the
    two, then averaging with the sequence ratio, behaves well on both.
    """
    left_norm = normalize_for_match(left)
    right_norm = normalize_for_match(right)
    if not left_norm or not right_norm:
        return 0.0
    if left_norm == right_norm:
        return 1.0

    sequence = SequenceMatcher(None, left_norm, right_norm).ratio()

    left_tokens = {t for t in left_norm.split() if t not in _STOPWORDS}
    right_tokens = {t for t in right_norm.split() if t not in _STOPWORDS}
    if left_tokens and right_tokens:
        overlap = len(left_tokens & right_tokens)
        # Containment, not Jaccard: a stored title is often the full title with
        # a subtitle the citation dropped.
        token = overlap / min(len(left_tokens), len(right_tokens))
    else:
        token = 0.0

    return max(sequence, (sequence + token) / 2)


def author_overlap(local: List[str], remote: List[str]) -> float:
    """Fraction of the shorter author list whose surnames appear in the other."""
    if not local or not remote:
        return 0.0
    local_keys = {name_key(name) for name in local if name_key(name)}
    remote_keys = {name_key(name) for name in remote if name_key(name)}
    if not local_keys or not remote_keys:
        return 0.0
    return len(local_keys & remote_keys) / min(len(local_keys), len(remote_keys))


def year_proximity(local: str, remote: str, tolerance: int = 1) -> float:
    """1.0 for an exact year, tapering to 0.0 beyond the tolerance."""
    if not local or not remote:
        return 0.5  # unknown, so neither reward nor punish
    try:
        difference = abs(int(local) - int(remote))
    except ValueError:
        return 0.5
    if difference == 0:
        return 1.0
    if difference <= tolerance:
        return 0.75
    if difference <= tolerance + 2:
        return 0.25
    return 0.0


def score(
    local_title: str,
    local_authors: List[str],
    local_year: str,
    local_doi: Optional[str],
    record: Record,
    year_tolerance: int = 1,
) -> MatchScore:
    """Score one candidate record against the local entry."""
    result = MatchScore()
    result.title = title_similarity(local_title, record.title)
    result.authors = author_overlap(local_authors, record.authors)
    result.year = year_proximity(local_year, record.year, year_tolerance)
    result.contains = token_containment(local_title, record.title)
    result.authors_comparable = bool(local_authors) and bool(record.authors)

    if local_doi and record.doi and local_doi.lower() == record.doi.lower():
        result.doi_exact = True

    result.overall = 0.70 * result.title + 0.20 * result.authors + 0.10 * result.year
    if result.doi_exact:
        result.overall = max(result.overall, 0.99)
    return result


def classify(
    result: MatchScore,
    accept_threshold: float,
    review_threshold: float,
    year_tolerance: int = 1,
) -> str:
    """Return 'verified', 'fuzzy' or 'no-match' for a scored candidate."""
    # A DOI both sides agree on is decisive.
    if result.doi_exact:
        return "verified"

    # Two papers can share almost all of a title and still be different work
    # ("Scaling Vision with Sparse MoE" vs "Scaling Vision-Language Models with
    # Sparse MoE"). Sharing no author at all is decisive evidence of that, and
    # accepting such a match is the worst outcome available: it grafts one
    # paper's venue, year and pages onto another's authors.
    if result.authors_comparable and result.authors == 0.0:
        return "fuzzy" if result.title >= review_threshold else "no-match"

    if result.title >= accept_threshold:
        return "verified"

    if result.title >= review_threshold:
        # A weaker title match is convincing only when one title is essentially
        # a truncation of the other. Same-author-same-year is not enough on its
        # own: prolific authors publish related work in the same year, and
        # "Sequence Transduction with RNNs" and "Supervised Sequence Labelling
        # with RNNs" are both Graves 2012 — and are different works.
        if result.contains >= 0.9 and result.authors >= 0.6 and result.year >= 0.75:
            return "verified"
        return "fuzzy"

    # Citations often truncate a title to its main clause, which tanks sequence
    # similarity. If every word of the shorter title appears in the longer one
    # and the authors and year agree, it is the same work.
    if result.contains >= 0.9:
        if result.authors >= 0.6 and result.year >= 0.75:
            return "verified"
        if result.authors >= 0.6 or result.year >= 0.75:
            return "fuzzy"

    return "no-match"


def best_candidate(
    candidates: List[Record],
    local_title: str,
    local_authors: List[str],
    local_year: str,
    local_doi: Optional[str],
    year_tolerance: int = 1,
) -> Optional[tuple]:
    """Pick the highest-scoring record. Returns (record, MatchScore) or None.

    Databases usually hold both the preprint and the published version of a
    paper under the same title. They score identically, so published records get
    a small ranking bonus to break the tie — the citation should point at the
    version of record. The bonus affects ranking only, never the reported score.
    """
    best = None
    best_rank = -1.0
    for record in candidates:
        if not record or record.is_empty():
            continue
        result = score(
            local_title, local_authors, local_year, local_doi, record, year_tolerance
        )
        rank = result.overall
        if record.kind not in ("preprint", "misc"):
            rank += 0.03
        if rank > best_rank:
            best = (record, result)
            best_rank = rank
    return best
