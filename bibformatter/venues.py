"""Venue canonicalization: "Proc. Int. Conf. Mach. Learn. (ICML)" ->
"International Conference on Machine Learning (ICML)".

The table below covers the venues common in ML / speech / NLP bibliographies.
Anything unknown is still cleaned of proceedings boilerplate and keeps whatever
acronym it already carried; extra venues can be added via `venues.acronyms` in
the config file.
"""

from __future__ import annotations

import re
from typing import Dict, List, Optional, Tuple

from bibformatter.normalize import normalize_for_match

# acronym -> (full name, kind, [alias fragments])
# Alias fragments are matched against the normalized venue string.
CANONICAL: Dict[str, Tuple[str, str, List[str]]] = {
    # ---- conferences ------------------------------------------------------
    "ICASSP": (
        "IEEE International Conference on Acoustics, Speech and Signal Processing",
        "conference",
        ["international conference on acoustics speech and signal processing",
         "int conf acoust speech signal process"],
    ),
    "ICLR": (
        "International Conference on Learning Representations",
        "conference",
        ["international conference on learning representations",
         "int conf learn representations"],
    ),
    "ICML": (
        "International Conference on Machine Learning",
        "conference",
        ["international conference on machine learning",
         "international conference on international conference on machine learning",
         "int conf mach learn"],
    ),
    "NeurIPS": (
        "Conference on Neural Information Processing Systems",
        "conference",
        ["neural information processing systems", "conf neural inf process syst",
         "advances in neural information processing systems", "nips"],
    ),
    "CVPR": (
        "IEEE/CVF Conference on Computer Vision and Pattern Recognition",
        "conference",
        ["conference on computer vision and pattern recognition"],
    ),
    "ICCV": ("IEEE/CVF International Conference on Computer Vision", "conference",
             ["international conference on computer vision"]),
    "ECCV": ("European Conference on Computer Vision", "conference",
             ["european conference on computer vision"]),
    "ACL": (
        "Annual Meeting of the Association for Computational Linguistics",
        "conference",
        ["annual meeting of the association for computational linguistics",
         "annu meeting assoc comput linguistics",
         "annual meeting of the acl"],
    ),
    "EMNLP": (
        "Conference on Empirical Methods in Natural Language Processing",
        "conference",
        ["empirical methods in natural language processing"],
    ),
    "NAACL": (
        "Conference of the North American Chapter of the Association for "
        "Computational Linguistics",
        "conference",
        ["north american chapter of the association for computational linguistics"],
    ),
    "Interspeech": (
        "Annual Conference of the International Speech Communication Association",
        "conference",
        ["interspeech", "annual conference of the international speech communication"],
    ),
    "ASRU": (
        "IEEE Automatic Speech Recognition and Understanding Workshop",
        "conference",
        ["automatic speech recognition and understanding",
         "autom speech recognit understanding"],
    ),
    "SLT": ("IEEE Spoken Language Technology Workshop", "conference",
            ["spoken language technology workshop"]),
    "CHiME": (
        "International Workshop on Speech Processing in Everyday Environments",
        "conference",
        ["speech processing in everyday environments", "chime"],
    ),
    "ICPR": ("International Conference on Pattern Recognition", "conference",
             ["international conference on pattern recognition"]),
    "KDD": (
        "ACM SIGKDD International Conference on Knowledge Discovery and Data Mining",
        "conference",
        ["knowledge discovery and data mining", "knowl discov data mining"],
    ),
    "WWW": ("International Conference on World Wide Web", "conference",
            ["international conference on world wide web"]),
    "AAAI": ("AAAI Conference on Artificial Intelligence", "conference",
             ["aaai conference on artificial intelligence"]),
    "IJCAI": ("International Joint Conference on Artificial Intelligence", "conference",
              ["international joint conference on artificial intelligence"]),
    "MCS": ("International Workshop on Multiple Classifier Systems", "conference",
            ["multiple classifier syst", "multiple classifier systems"]),
    "COLT": ("Annual Conference on Learning Theory", "conference",
             ["conference on learning theory"]),
    "SIGIR": (
        "International ACM SIGIR Conference on Research and Development in "
        "Information Retrieval",
        "conference",
        ["research and development in information retrieval"],
    ),
    # ---- journals ---------------------------------------------------------
    "TASLP": (
        "IEEE/ACM Transactions on Audio, Speech, and Language Processing",
        "journal",
        ["transactions on audio speech and language processing",
         "transactions on audio speech language processing"],
    ),
    "TPAMI": (
        "IEEE Transactions on Pattern Analysis and Machine Intelligence",
        "journal",
        ["transactions on pattern analysis and machine intelligence"],
    ),
    "TNNLS": (
        "IEEE Transactions on Neural Networks and Learning Systems",
        "journal",
        ["transactions on neural networks and learning systems"],
    ),
    "TSP": ("IEEE Transactions on Signal Processing", "journal",
            ["ieee transactions on signal processing"]),
    "TAFFC": ("IEEE Transactions on Affective Computing", "journal",
              ["transactions on affective computing"]),
    "JSTSP": (
        "IEEE Journal of Selected Topics in Signal Processing", "journal",
        ["journal of selected topics in signal processing"],
    ),
    "OJSP": ("IEEE Open Journal of Signal Processing", "journal",
             ["open journal of signal processing"]),
    "SPL": ("IEEE Signal Processing Letters", "journal",
            ["ieee signal processing letters"]),
    "SPM": ("IEEE Signal Processing Magazine", "journal",
            ["ieee signal processing magazine"]),
    "JMLR": ("Journal of Machine Learning Research", "journal",
             ["journal of machine learning research", "jmlr"]),
    "TACL": (
        "Transactions of the Association for Computational Linguistics", "journal",
        ["transactions of the association for computational linguistics"],
    ),
    "CSL": ("Computer Speech & Language", "journal",
            ["computer speech and language", "comput speech lang"]),
}

# Journals whose full name is standard but which have no acronym in common use.
# Listed here so abbreviated forms still get expanded.
EXPANSIONS: Dict[str, str] = {
    "neural comput": "Neural Computation",
    "neural computation": "Neural Computation",
    "pattern recognit": "Pattern Recognition",
    "pattern recognition": "Pattern Recognition",
    "adv comput": "Advances in Computers",
    "machine learning": "Machine Learning",
    "neural networks": "Neural Networks",
    "neurocomputing": "Neurocomputing",
    "annals of statistics": "The Annals of Statistics",
    "the annals of statistics": "The Annals of Statistics",
    "siam journal on optimization": "SIAM Journal on Optimization",
    "siam review": "SIAM Review",
    "evolutionary computation": "Evolutionary Computation",
    "international journal of forecasting": "International Journal of Forecasting",
    "foundations and trends in machine learning": "Foundations and Trends in Machine Learning",
    "mathematics of control signals and systems": "Mathematics of Control, Signals and Systems",
}

# Leading noise to strip: "Proc.", "Proceedings of the 35th", "2018", ...
# Deliberately does NOT strip "International" or "Annual": those are part of the
# venue's actual name ("International Conference on Machine Learning").
_BOILERPLATE = re.compile(
    r"^\s*(?:proc(?:\.|eedings)?\s*(?:\\)?\s*(?:of\s+)?(?:the\s+)?)?"
    r"(?:\d{4}\s+)?(?:\d+(?:st|nd|rd|th)\s+)?",
    re.IGNORECASE,
)
# Trailing noise: a year, "Proceedings", edition numbers.
_TRAILING = re.compile(r"\s*(?:,?\s*\d{4})?\s*(?:proceedings)?\s*$", re.IGNORECASE)
# A parenthesised acronym, optionally with a year: "(CHiME 2023)", "(ICPR2012)".
_ACRONYM_IN_PARENS = re.compile(r"\(\s*([A-Za-z][A-Za-z\-/&]{1,14})\s*\-?\s*\d{0,4}\s*\)")


def extract_acronym(raw: str) -> Optional[str]:
    """Pull a parenthesised acronym out of a venue string, if present."""
    matches = _ACRONYM_IN_PARENS.findall(raw or "")
    for candidate in reversed(matches):  # trailing one is the venue's own
        # Require at least two capitals, so "(Poster)" isn't taken as an acronym.
        if sum(1 for ch in candidate if ch.isupper()) >= 2:
            return candidate
    return None


def _lookup_by_acronym(acronym: str) -> Optional[Tuple[str, str, str]]:
    for key, (name, kind, _aliases) in CANONICAL.items():
        if key.lower() == acronym.lower():
            return (name, key, kind)
    return None


def _lookup_by_alias(normalized: str) -> Optional[Tuple[str, str, str]]:
    best: Optional[Tuple[str, str, str]] = None
    best_len = 0
    for acronym, (name, kind, aliases) in CANONICAL.items():
        for alias in aliases:
            if alias in normalized and len(alias) > best_len:
                best = (name, acronym, kind)
                best_len = len(alias)
    return best


def strip_boilerplate(raw: str) -> str:
    """Remove 'Proc. of the 35th', leading years and trailing 'Proceedings'."""
    text = re.sub(r"\s+", " ", (raw or "").strip())
    text = _ACRONYM_IN_PARENS.sub("", text).strip().rstrip(",").strip()
    previous = None
    while previous != text:  # boilerplate stacks: "Proc. of the 5th Int. Conf."
        previous = text
        text = _BOILERPLATE.sub("", text, count=1).strip()
    text = _TRAILING.sub("", text).strip().rstrip(",-").strip()
    return text


def canonicalize(
    raw: Optional[str],
    extra_acronyms: Optional[Dict[str, str]] = None,
    append_acronym: bool = True,
    do_strip: bool = True,
) -> Tuple[str, Optional[str], Optional[str]]:
    """Return (display_name, acronym, kind) for a venue string.

    `display_name` is the full venue name with "(ACRONYM)" appended when one is
    known and `append_acronym` is set.
    """
    if not raw or not raw.strip():
        return ("", None, None)

    raw = re.sub(r"\s+", " ", raw.strip())
    extra = {normalize_for_match(k): v for k, v in (extra_acronyms or {}).items()}

    acronym = extract_acronym(raw)
    core = strip_boilerplate(raw) if do_strip else raw
    normalized = normalize_for_match(core)

    # An arXiv "journal" is not a venue at all.
    if normalized.startswith("arxiv"):
        return ("", None, "preprint")

    hit = None
    if acronym:
        hit = _lookup_by_acronym(acronym)
    if hit is None:
        hit = _lookup_by_alias(normalized)
    # A bare acronym used as the whole venue ("ICML", "NIPS", "JMLR").
    if hit is None and normalized:
        hit = _lookup_by_acronym(normalized)

    if hit:
        name, found_acronym, kind = hit
        display = f"{name} ({found_acronym})" if append_acronym else name
        return (display, found_acronym, kind)

    # User-supplied acronyms from the config.
    for alias_norm, alias_acronym in extra.items():
        if alias_norm and alias_norm in normalized:
            display = f"{core} ({alias_acronym})" if append_acronym else core
            return (display, alias_acronym, None)

    # A known full journal name with no standard acronym.
    for alias, full in EXPANSIONS.items():
        if normalized == alias or normalized.startswith(alias + " "):
            return (full, None, "journal")

    # Unknown venue: keep it, re-attaching any acronym it already had.
    if acronym and append_acronym:
        return (f"{core} ({acronym})", acronym, None)
    return (core, acronym, None)
