"""Command line interface."""

from __future__ import annotations

import argparse
import json
import logging
import os
import sys
from concurrent.futures import ThreadPoolExecutor
from typing import Any, Dict, List

from bibformatter import __version__, parser as bibparser, report, schema, writer
from bibformatter.config import ConfigError, load_config
from bibformatter.net import HttpClient
from bibformatter.verify import (
    FUZZY,
    SKIPPED,
    UNVERIFIED,
    VERIFIED,
    Verification,
    Verifier,
)

log = logging.getLogger("bibformatter")

HEADER = (
    "% Formatted by bib-formatter {version}.\n"
    "% Entries verified against: {providers}.\n\n"
)


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="bib-formatter",
        description=(
            "Verify BibTeX entries against Crossref, arXiv, DBLP, OpenAlex and "
            "Semantic Scholar, then rewrite them in a consistent format."
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=(
            "examples:\n"
            "  bib-formatter refs.bib -o clean.bib\n"
            "  bib-formatter refs.bib -o clean.bib -c config.yaml -v\n"
            "  bib-formatter refs.bib --offline -o clean.bib   # formatting only\n"
            "  bib-formatter refs.bib --check                  # verify, write nothing\n"
        ),
    )
    parser.add_argument("input", help="input .bib file")
    parser.add_argument("-o", "--output", help="output .bib file (default: stdout)")
    parser.add_argument("-c", "--config", help="YAML or JSON config file")
    parser.add_argument(
        "--check",
        action="store_true",
        help="verify and report only; do not write a .bib file",
    )
    parser.add_argument(
        "--strict",
        action="store_true",
        help="exit non-zero if anything is unverified or still MISSING",
    )
    parser.add_argument(
        "--offline",
        action="store_true",
        help="no network and no verification: reformat what is already in the file",
    )
    parser.add_argument(
        "--cached-only",
        action="store_true",
        help="verify using only cached responses; make no new requests",
    )
    parser.add_argument(
        "--no-verify",
        action="store_true",
        help="skip database verification but still resolve DOIs and short DOIs",
    )
    parser.add_argument(
        "--missing",
        choices=["placeholder", "omit"],
        help="override the missing-data policy",
    )
    parser.add_argument(
        "--providers",
        help="comma-separated provider order, e.g. crossref,dblp,openalex",
    )
    parser.add_argument(
        "--mailto",
        help="contact email; gets you Crossref's faster polite pool",
    )
    parser.add_argument(
        "-j", "--workers", type=int, default=4,
        help="entries to process in parallel (default: 4)",
    )
    parser.add_argument("--report", help="write a JSON report to this path")
    parser.add_argument(
        "--clear-cache", action="store_true", help="delete the response cache first"
    )
    parser.add_argument("--no-color", action="store_true", help="disable coloured output")
    parser.add_argument("-v", "--verbose", action="store_true", help="list every correction")
    parser.add_argument(
        "-q", "--quiet", action="store_true",
        help="suppress the report unless something needs attention",
    )
    parser.add_argument("--debug", action="store_true", help="verbose logging")
    parser.add_argument("--version", action="version", version=f"%(prog)s {__version__}")
    return parser


def apply_overrides(config: Dict[str, Any], args: argparse.Namespace) -> None:
    if args.offline:
        config["network"]["enabled"] = False
        config["verification"]["enabled"] = False
    if args.cached_only:
        # Verification stays on; the HTTP client serves cache hits and returns
        # nothing for anything it hasn't already fetched.
        config["network"]["enabled"] = False
    if args.no_verify:
        config["verification"]["enabled"] = False
    if args.missing:
        config["missing"]["policy"] = args.missing
    if args.providers:
        config["verification"]["providers"] = [
            p.strip() for p in args.providers.split(",") if p.strip()
        ]
    if args.mailto:
        config["network"]["mailto"] = args.mailto


def process_all(
    raw_entries: List[Dict[str, str]],
    raw_sources: Dict[str, str],
    config: Dict[str, Any],
    verifier: Verifier,
    workers: int,
    progress: bool,
) -> List[schema.ProcessedEntry]:
    passthrough_types = {t.lower() for t in config["passthrough_types"]}
    total = len(raw_entries)
    done = [0]

    def handle(raw: Dict[str, str]) -> schema.ProcessedEntry:
        entry_type = (raw.get("ENTRYTYPE") or "").lower()
        local = schema.read_local(raw)

        if entry_type in passthrough_types:
            # Control entries are copied verbatim from the source when we have
            # it, so their original casing and field order survive.
            original = raw_sources.get(local.key)
            result = schema.ProcessedEntry(
                key=local.key,
                entry_type=entry_type,
                fields={},
                verification=Verification(status=SKIPPED),
                local=local,
                passthrough=original
                or bibparser.render_passthrough(raw, config["output"]["indent"]),
            )
        else:
            verification = verifier.verify(
                title=local.title,
                authors=local.authors,
                year=local.year,
                doi=local.doi,
                arxiv_id=local.arxiv_id,
            )
            result = schema.build_entry(
                local, verification, config, verifier.short_doi
            )

        done[0] += 1
        if progress:
            marker = {VERIFIED: ".", FUZZY: "?", UNVERIFIED: "x"}.get(
                result.verification.status, "-"
            )
            sys.stderr.write(marker)
            if done[0] % 50 == 0 or done[0] == total:
                sys.stderr.write(f" {done[0]}/{total}\n")
            sys.stderr.flush()
        return result

    if workers > 1:
        with ThreadPoolExecutor(max_workers=workers) as pool:
            results = list(pool.map(handle, raw_entries))
    else:
        results = [handle(raw) for raw in raw_entries]

    if progress and total:
        sys.stderr.write("\n")
    return results


def main(argv: List[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    logging.basicConfig(
        level=logging.DEBUG if args.debug else logging.WARNING,
        format="%(levelname)s %(name)s: %(message)s",
        stream=sys.stderr,
    )

    try:
        config = load_config(args.config)
    except ConfigError as exc:
        print(f"config error: {exc}", file=sys.stderr)
        return 2
    apply_overrides(config, args)

    if args.clear_cache:
        cache_path = config["network"].get("cache_path")
        if cache_path and os.path.exists(cache_path):
            os.remove(cache_path)

    try:
        raw_entries, raw_sources = bibparser.load_entries(args.input)
    except bibparser.ParseError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 2

    if not raw_entries:
        print(f"error: no entries found in {args.input}", file=sys.stderr)
        return 2

    duplicates = bibparser.find_duplicate_keys(raw_entries)

    http = HttpClient(config)
    verifier = Verifier(config, http)

    # Progress goes to stderr, so it never contaminates piped .bib output.
    progress = not args.quiet and sys.stderr.isatty()
    if progress:
        databases = ", ".join(config["verification"]["providers"])
        target = databases if verifier.enabled else "local data only"
        print(
            f"Processing {len(raw_entries)} entries against {target} "
            f"(. verified  ? review  x not found)",
            file=sys.stderr,
        )

    try:
        results = process_all(
            raw_entries, raw_sources, config, verifier, max(1, args.workers), progress
        )
    except KeyboardInterrupt:
        http.close()  # persist whatever we learned before the interrupt
        print("\ninterrupted; cache saved", file=sys.stderr)
        return 130
    finally:
        http.close()

    # BibTeX refuses to build a file with a repeated key, so the output carries
    # one entry per key regardless of what the input held.
    results, dropped = writer.deduplicate(results, config)

    summary = report.summarize(results, config)
    summary["duplicate_keys"] = duplicates
    summary["dropped_duplicates"] = dropped
    summary["dead_hosts"] = http.dead_hosts

    # Writing the .bib
    if not args.check:
        header = HEADER.format(
            version=__version__,
            providers=", ".join(config["verification"]["providers"])
            if verifier.enabled
            else "not verified (offline)",
        )
        text = writer.write_bib(results, config, header)
        if args.output:
            try:
                with open(args.output, "w", encoding="utf-8") as handle:
                    handle.write(text)
            except OSError as exc:
                print(f"error: could not write {args.output}: {exc}", file=sys.stderr)
                return 2
        else:
            sys.stdout.write(text)

    # Reporting: to stdout when the .bib went to a file, else to stderr.
    stream = sys.stdout if (args.output or args.check) else sys.stderr
    if not args.quiet or summary["unverified"] or summary["invalid_entries"]:
        stream.write(
            report.render(
                summary,
                config,
                net_stats=http.stats,
                verbose=args.verbose,
                color=not args.no_color,
            )
        )

    if args.report:
        try:
            with open(args.report, "w", encoding="utf-8") as handle:
                json.dump(summary, handle, indent=2, ensure_ascii=False)
        except OSError as exc:
            print(f"warning: could not write report: {exc}", file=sys.stderr)

    if args.strict and (
        summary["unverified"]
        or summary["missing_entries"]
        or summary["invalid_entries"]
        or duplicates
    ):
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
