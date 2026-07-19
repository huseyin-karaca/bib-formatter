"""Entry verification: prove each reference exists, then correct it.

For every entry we walk the configured databases in order and stop at the first
confident match. An entry that no database recognises is reported as
`unverified` — the signal that a reference may be fabricated.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bibformatter import matching
from bibformatter.matching import MatchScore
from bibformatter.normalize import latex_to_text, looks_like_initials, parse_name
from bibformatter.providers import (
    MISC,
    PREPRINT,
    Record,
    ShortDoiProvider,
    build_providers,
)

# Kinds that don't tell us where the work was actually published, so finding one
# is a reason to keep asking other databases rather than to stop.
INCONCLUSIVE_KINDS = (PREPRINT, MISC)

log = logging.getLogger(__name__)

VERIFIED = "verified"
FUZZY = "fuzzy"
UNVERIFIED = "unverified"
SKIPPED = "skipped"


@dataclass
class Attempt:
    """One provider's best answer, kept for the report."""

    provider: str
    found: bool
    best_title: str = ""
    score: Optional[MatchScore] = None
    verdict: str = "no-match"


@dataclass
class Verification:
    status: str = UNVERIFIED
    record: Optional[Record] = None
    score: Optional[MatchScore] = None
    source: str = ""
    attempts: List[Attempt] = field(default_factory=list)
    notes: List[str] = field(default_factory=list)

    @property
    def trusted(self) -> bool:
        return self.status == VERIFIED


class Verifier:
    def __init__(self, config: Dict[str, Any], http):
        self.config = config
        self.http = http
        self.settings = config["verification"]
        self.providers = build_providers(config, http)
        self.shortdoi = ShortDoiProvider(http)
        # Deliberately independent of network.enabled: with requests switched
        # off the HTTP client still serves cached responses, which is what
        # --cached-only replays. Providers simply come back empty for anything
        # not already fetched.
        self.enabled = self.settings["enabled"]

    # -- main entry point ----------------------------------------------------

    def verify(
        self,
        title: str,
        authors: List[str],
        year: str,
        doi: Optional[str],
        arxiv_id: Optional[str],
    ) -> Verification:
        result = Verification()
        if not self.enabled:
            result.status = SKIPPED
            result.notes.append("verification disabled")
            return result

        plain_title = latex_to_text(title)
        plain_authors = [latex_to_text(a) for a in authors]

        # Records that confirm the work exists but not where it was published.
        # Held back in case a later database knows the published version.
        preprint_fallback = None

        # 1. A DOI is the strongest identifier available — try it first.
        if doi:
            found = self._lookup_by_doi(doi, plain_title, plain_authors, year, result)
            if found and found.kind not in INCONCLUSIVE_KINDS:
                return self._finalize(found, result, plain_title, plain_authors, year)
            if found:
                # An arXiv DOI (10.48550/...) resolves to the preprint. Keep
                # looking for the version of record before settling for it.
                preprint_fallback = found

        # 2. An arXiv id resolves to the preprint, and often to the published DOI.
        if arxiv_id:
            found = self._lookup_by_arxiv(arxiv_id, plain_title, plain_authors, year, result)
            if found and found.kind not in INCONCLUSIVE_KINDS:
                return self._finalize(found, result, plain_title, plain_authors, year)
            if found and preprint_fallback is None:
                preprint_fallback = found

        # 3. Search each database by title and author.
        found = self._search_all(plain_title, plain_authors, year, doi, result)
        if found:
            return self._finalize(found, result, plain_title, plain_authors, year)

        # 4. Nothing found a published version: it really is just a preprint.
        if preprint_fallback is not None:
            result.notes.append("no published version found; treating as a preprint")
            return self._finalize(
                preprint_fallback, result, plain_title, plain_authors, year
            )

        # Nothing convincing anywhere: keep the best near-miss for the report.
        if result.record is not None:
            result.status = FUZZY
        else:
            result.status = UNVERIFIED
            result.notes.append("no database returned a matching record")
        return result

    # -- lookup strategies ---------------------------------------------------

    def _lookup_by_doi(
        self,
        doi: str,
        title: str,
        authors: List[str],
        year: str,
        result: Verification,
    ) -> Optional[Record]:
        for name in ("crossref", "openalex", "semanticscholar"):
            provider = self.providers.get(name)
            if not provider or not hasattr(provider, "by_doi"):
                continue
            try:
                record = provider.by_doi(doi)
            except Exception as exc:  # a provider bug must not sink the run
                log.warning("%s DOI lookup failed: %s", name, exc)
                result.attempts.append(Attempt(provider=f"{name}:doi", found=False))
                continue
            if not record:
                result.attempts.append(Attempt(provider=f"{name}:doi", found=False))
                continue

            # Deliberately score without the DOI. We fetched this record *by*
            # that DOI, so a DOI match proves only that the DOI resolves — not
            # that it points at the work being cited. Letting it count would
            # rubber-stamp any entry carrying a real DOI from another paper,
            # which is exactly the fabrication this tool exists to catch.
            scored = matching.score(
                title, authors, year, None, record, self.settings["year_tolerance"]
            )
            verdict = matching.classify(
                scored,
                self.settings["accept_threshold"],
                self.settings["review_threshold"],
                self.settings["year_tolerance"],
            )
            result.attempts.append(
                Attempt(
                    provider=f"{name}:doi",
                    found=True,
                    best_title=record.title,
                    score=scored,
                    verdict=verdict,
                )
            )

            if verdict == VERIFIED:
                result.source = f"{name} (DOI)"
                result.score = scored
                return record

            # The DOI resolved, but to something else. That is itself a finding:
            # keep searching by title, and say so in the report.
            result.notes.append(
                f"DOI {doi} resolves to a different work: {record.title!r}"
            )
            self._remember_fallback(result, record, scored)
        return None

    def _lookup_by_arxiv(
        self,
        arxiv_id: str,
        title: str,
        authors: List[str],
        year: str,
        result: Verification,
    ) -> Optional[Record]:
        provider = self.providers.get("arxiv")
        if not provider:
            return None

        preprint = provider.by_id(arxiv_id)
        if not preprint:
            result.attempts.append(Attempt(provider="arxiv:id", found=False))
            return None

        scored = matching.score(
            title, authors, year, None, preprint, self.settings["year_tolerance"]
        )
        verdict = matching.classify(
            scored,
            self.settings["accept_threshold"],
            self.settings["review_threshold"],
            self.settings["year_tolerance"],
        )
        result.attempts.append(
            Attempt(
                provider="arxiv:id",
                found=True,
                best_title=preprint.title,
                score=scored,
                verdict=verdict,
            )
        )
        if verdict == "no-match":
            self._remember_fallback(result, preprint, scored)
            return None

        # arXiv knows the published DOI: prefer the published version's metadata,
        # which is what the citation should actually point at.
        if preprint.doi and self.config["links"]["resolve_arxiv_to_doi"]:
            crossref = self.providers.get("crossref")
            if crossref:
                published = crossref.by_doi(preprint.doi)
                if published:
                    published_score = matching.score(
                        title, authors, year, preprint.doi, published,
                        self.settings["year_tolerance"],
                    )
                    result.attempts.append(
                        Attempt(
                            provider="crossref:arxiv-doi",
                            found=True,
                            best_title=published.title,
                            score=published_score,
                            verdict=VERIFIED,
                        )
                    )
                    published.arxiv_id = preprint.arxiv_id or arxiv_id
                    result.notes.append(
                        f"arXiv:{arxiv_id} was published as {preprint.doi}"
                    )
                    result.source = "arxiv -> crossref"
                    result.score = published_score
                    return published

        if verdict == VERIFIED:
            result.source = "arxiv"
            result.score = scored
            # No published DOI found: it is genuinely still a preprint.
            preprint.kind = PREPRINT
            return preprint

        self._remember_fallback(result, preprint, scored)
        return None

    def _search_all(
        self,
        title: str,
        authors: List[str],
        year: str,
        doi: Optional[str],
        result: Verification,
    ) -> Optional[Record]:
        if not title:
            return None

        # A verified match that doesn't say where the work was published is kept
        # aside: another database may know the venue. Best one wins at the end.
        inconclusive = None

        for name in self.settings["providers"]:
            provider = self.providers.get(name)
            if not provider or not hasattr(provider, "search"):
                continue

            try:
                candidates = provider.search(title, authors, year)
            except Exception as exc:  # a provider bug must not sink the run
                log.warning("%s search failed: %s", name, exc)
                result.attempts.append(Attempt(provider=name, found=False))
                continue

            if not candidates:
                result.attempts.append(Attempt(provider=name, found=False))
                continue

            best = matching.best_candidate(
                candidates, title, authors, year, doi, self.settings["year_tolerance"]
            )
            if not best:
                result.attempts.append(Attempt(provider=name, found=False))
                continue

            record, scored = best
            verdict = matching.classify(
                scored,
                self.settings["accept_threshold"],
                self.settings["review_threshold"],
                self.settings["year_tolerance"],
            )
            result.attempts.append(
                Attempt(
                    provider=name,
                    found=True,
                    best_title=record.title,
                    score=scored,
                    verdict=verdict,
                )
            )

            if verdict == VERIFIED:
                if record.kind not in INCONCLUSIVE_KINDS:
                    result.source = name
                    result.score = scored
                    return record
                if inconclusive is None or scored.overall > inconclusive[1].overall:
                    inconclusive = (record, scored, name)
                continue

            if verdict == FUZZY:
                self._remember_fallback(result, record, scored)

        if inconclusive is not None:
            record, scored, name = inconclusive
            result.source = name
            result.score = scored
            return record

        return None

    @staticmethod
    def _remember_fallback(
        result: Verification, record: Record, scored: MatchScore
    ) -> None:
        """Keep the best sub-threshold candidate so the report can show it."""
        if result.score is None or scored.overall > result.score.overall:
            result.record = record
            result.score = scored
            result.source = record.source

    # -- post-processing -----------------------------------------------------

    def _finalize(
        self,
        record: Record,
        result: Verification,
        title: str,
        authors: List[str],
        year: str,
    ) -> Verification:
        result.status = VERIFIED
        result.record = record
        if self.config["authors"]["require_full_names"]:
            self._expand_initials(record, title, authors, result)
        return result

    def _expand_initials(
        self,
        record: Record,
        title: str,
        local_authors: List[str],
        result: Verification,
    ) -> None:
        """Crossref sometimes stores initials; other databases usually don't.

        Only replaces the author list if the surnames still line up.
        """
        abbreviated = [
            name for name in record.authors if looks_like_initials(parse_name(name)[0])
        ]
        if not abbreviated:
            return

        for name in ("dblp", "openalex", "semanticscholar"):
            if name == record.source:
                continue
            provider = self.providers.get(name)
            if not provider or not hasattr(provider, "search"):
                continue
            try:
                candidates = provider.search(record.title or title, record.authors, record.year)
            except Exception:
                continue
            for candidate in candidates:
                if matching.title_similarity(record.title, candidate.title) < 0.95:
                    continue
                if matching.author_overlap(record.authors, candidate.authors) < 0.6:
                    continue
                still_short = [
                    a for a in candidate.authors if looks_like_initials(parse_name(a)[0])
                ]
                if len(still_short) < len(abbreviated):
                    record.authors = candidate.authors
                    result.notes.append(f"expanded author initials using {name}")
                    return

    # -- links ---------------------------------------------------------------

    def short_doi(self, doi: str) -> Optional[str]:
        """Resolve a DOI to its short form, or None if the service can't."""
        if not doi:
            return None
        try:
            return self.shortdoi.shorten(doi)
        except Exception as exc:
            log.debug("shortdoi failed for %s: %s", doi, exc)
            return None
