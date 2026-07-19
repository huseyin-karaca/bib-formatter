"""Reading .bib files safely."""

from __future__ import annotations

import logging
import re
from typing import Any, Dict, List, Tuple

import bibtexparser
from bibtexparser.bparser import BibTexParser

log = logging.getLogger(__name__)


class ParseError(Exception):
    pass


def load_entries(path: str) -> Tuple[List[Dict[str, str]], Dict[str, str]]:
    """Parse a .bib file.

    Returns (entries, raw_sources). Values are left exactly as written (no LaTeX
    decoding) so nothing is lost before we decide what to do with it.
    `raw_sources` maps citation key -> the entry's original text, used to copy
    control entries through byte-for-byte.
    """
    try:
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
    except OSError as exc:
        raise ParseError(f"could not read {path}: {exc}") from exc

    parser = BibTexParser(common_strings=True)
    parser.ignore_nonstandard_types = False  # keep @online, @IEEEtranBSTCTL, ...
    parser.homogenise_fields = False
    parser.interpolate_strings = True

    try:
        database = bibtexparser.loads(text, parser)
    except Exception as exc:
        raise ParseError(f"could not parse {path}: {exc}") from exc

    if not database.entries:
        log.warning("no entries found in %s", path)
    return database.entries, _raw_sources(text)


def _raw_sources(text: str) -> Dict[str, str]:
    """Slice the original file into per-entry text, keyed by citation key.

    Brace counting rather than a regex, because entry bodies nest braces.
    """
    sources: Dict[str, str] = {}
    for match in re.finditer(r"@(\w+)\s*\{\s*([^,\s}]+)\s*,", text):
        key = match.group(2)
        depth = 0
        index = text.index("{", match.start())
        for position in range(index, len(text)):
            character = text[position]
            if character == "{":
                depth += 1
            elif character == "}":
                depth -= 1
                if depth == 0:
                    sources.setdefault(key, text[match.start() : position + 1])
                    break
    return sources


def find_duplicate_keys(entries: List[Dict[str, str]]) -> Dict[str, int]:
    """Citation keys used by more than one entry (BibTeX silently drops these)."""
    counts: Dict[str, int] = {}
    for entry in entries:
        key = entry.get("ID", "")
        if key:
            counts[key] = counts.get(key, 0) + 1
    return {key: count for key, count in counts.items() if count > 1}


def render_passthrough(entry: Dict[str, Any], indent: str = "  ") -> str:
    """Re-emit a control entry (@IEEEtranBSTCTL and friends) unchanged."""
    entry_type = entry.get("ENTRYTYPE", "misc")
    key = entry.get("ID", "")
    fields = [(k, v) for k, v in entry.items() if k not in ("ID", "ENTRYTYPE")]
    if not fields:
        return f"@{entry_type}{{{key}}}"

    width = max(len(k) for k, _ in fields)
    lines = [f"@{entry_type}{{{key},"]
    for index, (name, value) in enumerate(fields):
        comma = "," if index < len(fields) - 1 else ""
        lines.append(f"{indent}{name.ljust(width)} = {{{value}}}{comma}")
    lines.append("}")
    return "\n".join(lines)
