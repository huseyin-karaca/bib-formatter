"""Text, name, page and identifier normalization.

Two directions matter here:
  * LaTeX -> Unicode, for comparing local entries against database records.
  * Unicode -> LaTeX, for writing database records back into a .bib file.
"""

from __future__ import annotations

import re
import unicodedata
from typing import List, Optional

from pylatexenc.latex2text import LatexNodes2Text
from pylatexenc.latexencode import UnicodeToLatexEncoder

_TO_TEXT = LatexNodes2Text(math_mode="text", keep_comments=False)
# "keep" leaves characters pylatexenc has no rule for as literal UTF-8, which
# modern LaTeX handles fine and is far less lossy than dropping them.
_TO_LATEX = UnicodeToLatexEncoder(unknown_char_policy="keep")


def latex_to_text(value: Optional[str]) -> str:
    """Convert a LaTeX-encoded BibTeX value into plain Unicode."""
    if not value:
        return ""
    text = value.replace("\n", " ")
    try:
        text = _TO_TEXT.latex_to_text(text)
    except Exception:
        # pylatexenc chokes on genuinely broken markup; fall back to a crude strip.
        text = re.sub(r"\\[a-zA-Z]+", " ", text)
    text = text.replace("{", "").replace("}", "")
    # Typographic hyphens sneak in from PDFs and break name matching.
    text = text.replace("‐", "-").replace("‑", "-")
    return re.sub(r"\s+", " ", text).strip()


def text_to_latex(value: Optional[str], protect_caps: bool = False) -> str:
    """Convert plain Unicode into a safe BibTeX value.

    Input must be plain text, not LaTeX: pass anything from a .bib file through
    `latex_to_text` first, or its backslashes get escaped a second time.
    Accents and the specials (& % # _) are handled by pylatexenc.

    `protect_caps` braces acronyms and other all-caps runs so BibTeX styles
    can't lowercase them (GShard, ICLR, CHiME).
    """
    if not value:
        return ""
    try:
        out = _TO_LATEX.unicode_to_latex(value)
    except Exception:
        out = value

    if protect_caps:
        out = _protect_capitals(out)
    return out


def _needs_protection(word: str) -> bool:
    """True for acronyms and camelCase, false for ordinary Title Case.

    BibTeX styles lowercase title words, which would turn ASR into "asr" and
    PyTorch into "Pytorch". But bracing every capitalised word produces noise
    like {Self-Supervised}, so protect only what would actually be corrupted:
    a run of two capitals (ASR, GShard, CHiME) or a capital following a
    lowercase letter (PyTorch, wav2vec).
    """
    if re.search(r"[A-Z]{2}", word):
        return True
    return bool(re.search(r"[a-z][A-Z]", word))


def _protect_capitals(text: str) -> str:
    """Brace acronyms so BibTeX styles can't lowercase them."""

    def repl(match: re.Match) -> str:
        word = match.group(0)
        return "{" + word + "}" if _needs_protection(word) else word

    # Whole words only, skipping anything already braced or part of a command.
    return re.sub(r"(?<![{\\\w])[A-Za-z][A-Za-z0-9\-]*(?![}\w])", repl, text)


def normalize_for_match(value: Optional[str]) -> str:
    """Aggressively normalize a title for similarity comparison."""
    text = latex_to_text(value).lower()
    text = unicodedata.normalize("NFKD", text)
    text = "".join(ch for ch in text if not unicodedata.combining(ch))
    text = re.sub(r"[^a-z0-9 ]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


# --------------------------------------------------------------------------
# Author names
# --------------------------------------------------------------------------

_SUFFIXES = {"jr", "jr.", "sr", "sr.", "ii", "iii", "iv"}
_PARTICLES = {"van", "von", "de", "del", "della", "der", "den", "di", "da", "du", "la", "le", "ten", "ter"}


def split_authors(raw: Optional[str]) -> List[str]:
    """Split a BibTeX author field on top-level ' and ' (brace-aware)."""
    if not raw:
        return []
    text = re.sub(r"\s+", " ", raw.replace("\n", " ")).strip()

    parts: List[str] = []
    depth = 0
    token: List[str] = []
    words = text.split(" ")
    index = 0
    while index < len(words):
        word = words[index]
        if word == "and" and depth == 0 and token:
            parts.append(" ".join(token))
            token = []
            index += 1
            continue
        depth += word.count("{") - word.count("}")
        token.append(word)
        index += 1
    if token:
        parts.append(" ".join(token))

    return [p.strip().rstrip(",").strip() for p in parts if p.strip()]


def is_corporate(name: str) -> bool:
    """A fully braced name ({NVIDIA}, {Mozilla Foundation}) is an organisation."""
    stripped = name.strip()
    return stripped.startswith("{") and stripped.endswith("}") and "{" not in stripped[1:-1]


def parse_name(name: str) -> tuple:
    """Split one author into (given, family). Handles 'Last, First' and 'First Last'."""
    name = name.strip()
    if is_corporate(name):
        return ("", name)

    if "," in name:
        family, _, given = name.partition(",")
        return (given.strip(), family.strip())

    words = [w for w in name.split() if w]
    if not words:
        return ("", "")
    if len(words) == 1:
        return ("", words[0])

    # Pull a trailing suffix off before deciding the family name.
    suffix = ""
    if latex_to_text(words[-1]).lower().strip(".") in {s.strip(".") for s in _SUFFIXES}:
        suffix = words.pop()

    # Walk back over particles so "Ludwig van Beethoven" keeps "van Beethoven".
    split_at = len(words) - 1
    while split_at > 1 and latex_to_text(words[split_at - 1]).lower() in _PARTICLES:
        split_at -= 1

    given = " ".join(words[:split_at])
    family = " ".join(words[split_at:])
    if suffix:
        family = f"{family} {suffix}"
    return (given.strip(), family.strip())


def looks_like_initials(given: str) -> bool:
    """True if the *first* given name is abbreviated ('J.', 'J. K.', 'JK').

    Only the first name matters. A middle initial is how many authors publish
    ("Aidan N. Gomez"), so flagging those would bury the real cases — an entry
    that says "Li, J." when the author is "Jinyu Li" — in noise.
    """
    text = latex_to_text(given).strip()
    if not text:
        return False

    first = text.split()[0].strip(".")
    if not first:
        return False
    if len(first) == 1:
        return True
    # "JK Rowling": a short all-caps run standing in for several given names.
    return len(first) <= 3 and first.isupper()


def format_name(given: str, family: str, order: str = "first-last") -> str:
    """Render one author in the configured order."""
    given = given.strip()
    family = family.strip()
    if not given:
        return family
    if not family:
        return given
    if order == "last-first":
        return f"{family}, {given}"
    return f"{given} {family}"


def name_key(name: str) -> str:
    """A comparable key for one author: the normalized family name."""
    _, family = parse_name(name)
    return normalize_for_match(family)


# --------------------------------------------------------------------------
# Pages, years, identifiers
# --------------------------------------------------------------------------

_DASHES = "‐‑‒–—―−"


def normalize_pages(value: Optional[str]) -> str:
    """Normalize a page range to BibTeX's '12--34' form."""
    if not value:
        return ""
    text = latex_to_text(value).strip()
    for dash in _DASHES:
        text = text.replace(dash, "-")
    text = re.sub(r"-{2,}", "-", text)
    text = re.sub(r"\s*-\s*", "-", text)

    match = re.match(r"^([A-Za-z]?\d+)-([A-Za-z]?\d+)$", text)
    if match:
        start, end = match.groups()
        if start == end:
            return start
        return f"{start}--{end}"
    return text


def normalize_year(value) -> str:
    """Extract a four-digit year from whatever form it arrived in."""
    if value is None:
        return ""
    match = re.search(r"(1[5-9]\d{2}|20\d{2}|21\d{2})", str(value))
    return match.group(1) if match else ""


_ARXIV_PATTERNS = [
    re.compile(r"arxiv\.org/(?:abs|pdf)/([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.I),
    re.compile(r"arxiv\.org/(?:abs|pdf)/([a-z\-]+(?:\.[A-Z]{2})?/\d{7})(?:v\d+)?", re.I),
    re.compile(r"arxiv[:\s]+([0-9]{4}\.[0-9]{4,5})(?:v\d+)?", re.I),
    re.compile(r"^([0-9]{4}\.[0-9]{4,5})(?:v\d+)?$"),
]


def extract_arxiv_id(*values: Optional[str]) -> Optional[str]:
    """Find an arXiv identifier in any of the given strings."""
    for value in values:
        if not value:
            continue
        text = str(value).strip()
        for pattern in _ARXIV_PATTERNS:
            match = pattern.search(text)
            if match:
                return match.group(1)
    return None


_DOI_RE = re.compile(r"\b(10\.\d{4,9}/[^\s{}\"<>,;]+)", re.I)


def extract_doi(*values: Optional[str]) -> Optional[str]:
    """Find a DOI in any of the given strings and clean its tail."""
    for value in values:
        if not value:
            continue
        match = _DOI_RE.search(latex_to_text(str(value)))
        if match:
            doi = match.group(1)
            # Strip trailing punctuation picked up from prose or \url{...}.
            doi = doi.rstrip(".,;)")
            while doi.endswith(")") and doi.count("(") < doi.count(")"):
                doi = doi[:-1]
            # Case is preserved: DOIs resolve case-insensitively, but publishers
            # register a canonical form and citations should reproduce it.
            return doi
    return None


def extract_url(*values: Optional[str]) -> Optional[str]:
    """Find the first http(s) URL in any of the given strings."""
    for value in values:
        if not value:
            continue
        match = re.search(r"https?://[^\s{}\"<>]+", str(value))
        if match:
            return match.group(0).rstrip(".,;")
    return None
