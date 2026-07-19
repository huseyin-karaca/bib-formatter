"""Human-readable and machine-readable reporting."""

from __future__ import annotations

import sys
from typing import Any, Dict, List

from bibformatter.normalize import latex_to_text
from bibformatter.schema import ProcessedEntry, validate
from bibformatter.verify import FUZZY, SKIPPED, UNVERIFIED, VERIFIED


class Style:
    """ANSI styling, disabled when not writing to a terminal."""

    def __init__(self, enabled: bool = True):
        self.enabled = enabled and sys.stdout.isatty()

    def _wrap(self, code: str, text: str) -> str:
        return f"\033[{code}m{text}\033[0m" if self.enabled else text

    def bold(self, text: str) -> str:
        return self._wrap("1", text)

    def red(self, text: str) -> str:
        return self._wrap("31", text)

    def yellow(self, text: str) -> str:
        return self._wrap("33", text)

    def green(self, text: str) -> str:
        return self._wrap("32", text)

    def dim(self, text: str) -> str:
        return self._wrap("2", text)


def summarize(results: List[ProcessedEntry], config: Dict[str, Any]) -> Dict[str, Any]:
    """Compute the counts and problem lists the report is built from."""
    entries = [r for r in results if r.passthrough is None]

    summary: Dict[str, Any] = {
        "total": len(entries),
        "passthrough": len(results) - len(entries),
        "verified": 0,
        "fuzzy": 0,
        "unverified": 0,
        "skipped": 0,
        "by_type": {},
        "unverified_entries": [],
        "fuzzy_entries": [],
        "missing_entries": [],
        "invalid_entries": [],
        "corrected_entries": [],
        "duplicate_keys": {},
    }

    for result in entries:
        status = result.verification.status
        summary[status] = summary.get(status, 0) + 1
        summary["by_type"][result.entry_type] = (
            summary["by_type"].get(result.entry_type, 0) + 1
        )

        if status == UNVERIFIED:
            summary["unverified_entries"].append(
                {
                    "key": result.key,
                    "title": latex_to_text(result.local.title),
                    "year": result.local.year,
                    "providers": [a.provider for a in result.verification.attempts],
                    "notes": result.verification.notes,
                }
            )
        elif status == FUZZY:
            record = result.verification.record
            score = result.verification.score
            summary["fuzzy_entries"].append(
                {
                    "key": result.key,
                    "title": latex_to_text(result.local.title),
                    "candidate": record.title if record else "",
                    "candidate_source": record.source if record else "",
                    "candidate_doi": record.doi if record else "",
                    "score": score.as_dict() if score else {},
                    "notes": result.verification.notes,
                }
            )

        if result.missing:
            summary["missing_entries"].append(
                {"key": result.key, "fields": result.missing}
            )

        problems = validate(result, config)
        if problems:
            summary["invalid_entries"].append({"key": result.key, "problems": problems})

        if result.changes:
            summary["corrected_entries"].append(
                {"key": result.key, "changes": result.changes}
            )

    return summary


def render(
    summary: Dict[str, Any],
    config: Dict[str, Any],
    net_stats: Dict[str, int] | None = None,
    verbose: bool = False,
    color: bool = True,
) -> str:
    style = Style(color)
    lines: List[str] = []

    def rule(title: str) -> None:
        lines.append("")
        lines.append(style.bold(title))
        lines.append("-" * len(title))

    total = summary["total"]
    rule("Summary")
    lines.append(f"  entries processed : {total}")
    if summary["passthrough"]:
        lines.append(f"  passed through    : {summary['passthrough']}")
    lines.append(
        "  "
        + style.green(f"verified          : {summary['verified']}")
    )
    if summary["fuzzy"]:
        lines.append("  " + style.yellow(f"needs review      : {summary['fuzzy']}"))
    if summary["unverified"]:
        lines.append("  " + style.red(f"UNVERIFIED        : {summary['unverified']}"))
    if summary["skipped"]:
        lines.append(f"  not checked       : {summary['skipped']}")
    if summary["by_type"]:
        types = ", ".join(
            f"{name} {count}" for name, count in sorted(summary["by_type"].items())
        )
        lines.append(f"  types             : {types}")

    if summary["unverified_entries"]:
        rule("Not found in any database — verify these by hand")
        lines.append(
            style.dim(
                "  These references could not be confirmed to exist. Check for "
                "typos,\n  or for a citation that was invented."
            )
        )
        for item in summary["unverified_entries"]:
            lines.append("")
            lines.append("  " + style.red(f"✗ {item['key']}"))
            lines.append(f"      title    : {item['title'] or '(none)'}")
            if item["year"]:
                lines.append(f"      year     : {item['year']}")
            lines.append(
                f"      searched : {', '.join(item['providers']) or 'nothing returned'}"
            )
            for note in item["notes"]:
                lines.append(f"      note     : {note}")

    if summary["fuzzy_entries"]:
        rule("Close but not conclusive — confirm before trusting")
        for item in summary["fuzzy_entries"]:
            lines.append("")
            lines.append("  " + style.yellow(f"? {item['key']}"))
            lines.append(f"      yours     : {item['title']}")
            lines.append(
                f"      candidate : {item['candidate']} "
                f"[{item['candidate_source']}]"
            )
            if item["candidate_doi"]:
                lines.append(f"      doi       : {item['candidate_doi']}")
            score = item["score"]
            if score:
                lines.append(
                    f"      score     : title {score.get('title')} "
                    f"authors {score.get('authors')} year {score.get('year')}"
                )
            for note in item["notes"]:
                lines.append(f"      note      : {note}")

    if summary.get("duplicate_keys"):
        rule("Duplicate citation keys")
        lines.append(
            style.dim("  BibTeX keeps only one entry per key; the others are lost.")
        )
        for key, count in sorted(summary["duplicate_keys"].items()):
            lines.append("  " + style.yellow(f"{key}: {count} entries"))

    if summary["missing_entries"]:
        rule(f"Entries with unresolved {config['missing']['placeholder']} fields")
        for item in summary["missing_entries"]:
            lines.append(f"  {item['key']}: {', '.join(item['fields'])}")

    if summary["invalid_entries"]:
        rule("Schema problems")
        for item in summary["invalid_entries"]:
            lines.append(f"  {item['key']}:")
            for problem in item["problems"]:
                lines.append(f"      - {problem}")

    if verbose and summary["corrected_entries"]:
        rule("Corrections applied")
        for item in summary["corrected_entries"]:
            lines.append(f"  {item['key']}:")
            for change in item["changes"]:
                lines.append(f"      - {change}")
    elif summary["corrected_entries"]:
        rule("Corrections applied")
        count = sum(len(i["changes"]) for i in summary["corrected_entries"])
        lines.append(
            f"  {count} corrections across {len(summary['corrected_entries'])} "
            "entries (use -v to list them)"
        )

    if net_stats:
        rule("Network")
        lines.append(
            f"  requests {net_stats.get('requests', 0)}, "
            f"cache hits {net_stats.get('cache_hits', 0)}, "
            f"retries {net_stats.get('retries', 0)}, "
            f"failures {net_stats.get('failures', 0)}"
        )
        if summary.get("dead_hosts"):
            # A run that lost a database is degraded, not wrong — but the user
            # should know an "unverified" verdict may just mean nobody answered.
            hosts = ", ".join(summary["dead_hosts"])
            lines.append(
                "  "
                + style.yellow(
                    f"unreachable, skipped after repeated failures: {hosts}"
                )
            )
            lines.append(
                style.dim(
                    "  Re-run to retry them; cached results are reused, so it "
                    "will be quick."
                )
            )

    lines.append("")
    return "\n".join(lines)
