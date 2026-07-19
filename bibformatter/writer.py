"""BibTeX output.

Written by hand rather than through bibtexparser's writer because the layout
rules are specific: fixed field order, aligned `=`, and one author per line
with the continuation lines aligned under the first.
"""

from __future__ import annotations

from typing import Any, Dict, List

from bibformatter.schema import ProcessedEntry


def _author_lines(value: str, separator: str, continuation: str) -> str:
    """Put each author on its own line, aligned under the first."""
    names = value.split(separator)
    if len(names) <= 1:
        return value
    joined = (separator.rstrip() + "\n" + continuation).join(
        name.strip() for name in names
    )
    return joined


def format_entry(result: ProcessedEntry, config: Dict[str, Any]) -> str:
    """Render one entry as BibTeX."""
    if result.passthrough is not None:
        return result.passthrough

    output = config["output"]
    indent = output["indent"]
    schema = config["schemas"].get(result.entry_type, list(result.fields))
    names = [f for f in schema if f in result.fields]

    width = max((len(f) for f in names), default=0) if output["align_values"] else 0

    lines = [f"@{result.entry_type}{{{result.key},"]
    for index, name in enumerate(names):
        value = result.fields[name]
        label = name.ljust(width)
        prefix = f"{indent}{label} = {{"
        if name == "author" and config["authors"]["one_per_line"]:
            value = _author_lines(
                value, config["authors"]["separator"], " " * len(prefix)
            )
        comma = "," if index < len(names) - 1 else ""
        lines.append(f"{prefix}{value}}}{comma}")
    lines.append("}")
    return "\n".join(lines)


def write_bib(
    results: List[ProcessedEntry], config: Dict[str, Any], header: str = ""
) -> str:
    """Render a whole bibliography."""
    output = config["output"]
    entries = list(results)
    if output["sort_by"] == "key":
        # Passthrough control entries stay at the top, where BibTeX expects them.
        entries.sort(
            key=lambda entry: (entry.passthrough is None, entry.key.lower())
        )

    separator = output.get("entry_separator", "\n")
    body = ("\n" + separator).join(format_entry(entry, config) for entry in entries)
    return (header + body + "\n") if header else (body + "\n")
