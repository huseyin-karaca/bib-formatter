# bib-formatter

Verifies BibTeX entries against real bibliographic databases, corrects their
metadata, and rewrites them in one consistent format.

The verification is the point. Reference lists — especially ones assembled with
help from a language model — routinely contain entries that look plausible but
do not exist, or real papers with the wrong venue, year or page numbers.
`bib-formatter` looks up every entry in Crossref, DBLP, OpenAlex, Semantic
Scholar and arXiv, tells you which references it could not find, and fixes the
ones it could.

```
$ bib-formatter reference.bib -o clean.bib

Summary
-------
  entries processed : 167
  verified          : 150
  needs review      : 10
  UNVERIFIED        : 7

Not found in any database — verify these by hand
------------------------------------------------
  ✗ liu2020amoestp
      title    : MOE-STP: Mixture-of-Experts for Scalable and Transferable Speech Translation
      searched : crossref:doi, openalex:doi, crossref, dblp, openalex, semanticscholar, arxiv
      note     : no database returned a matching record
```

## Install

```bash
pip install git+https://github.com/huseyin-karaca/bib-formatter.git
```

Or from a clone, for development:

```bash
git clone https://github.com/huseyin-karaca/bib-formatter.git
cd bib-formatter
pip install -e ".[dev]"
pytest
```

Requires Python 3.9+.

## Usage

```bash
bib-formatter refs.bib -o clean.bib              # verify, correct, rewrite
bib-formatter refs.bib -o clean.bib -c config.yaml -v
bib-formatter refs.bib --check                   # report only, write nothing
bib-formatter refs.bib --offline -o clean.bib    # formatting only, no network
bib-formatter refs.bib --check --strict          # exit 1 on any problem (CI)
```

The first run over a large bibliography takes a few minutes, because the
databases are rate-limited. Everything it learns is cached, so once you have a
full pass you can iterate on formatting for free:

```bash
bib-formatter refs.bib -o clean.bib --cached-only   # replays the cache, seconds
```

Give Crossref a contact address to land in their faster "polite pool":

```bash
bib-formatter refs.bib -o clean.bib --mailto you@example.com
# or: export CROSSREF_MAILTO=you@example.com
```

| Flag | Effect |
| --- | --- |
| `-o, --output` | Output file (default: stdout) |
| `-c, --config` | YAML or JSON config file |
| `--check` | Verify and report; write no `.bib` |
| `--strict` | Exit non-zero if anything is unverified, missing or duplicated |
| `--offline` | No network and no verification; just reformat the file |
| `--cached-only` | Verify from cached responses only; make no new requests |
| `--no-verify` | Skip database verification, still resolve DOIs and short DOIs |
| `--missing {placeholder,omit}` | Override the missing-data policy |
| `--providers` | Comma-separated provider order |
| `-j, --workers` | Entries processed in parallel (default 4) |
| `--report FILE` | Write the full report as JSON |
| `--clear-cache` | Delete the cached API responses first |
| `-v, --verbose` | List every correction that was applied |
| `-q, --quiet` | Suppress the report unless something needs attention |

## Output format

Three entry types, each with a fixed set of fields in a fixed order:

| Type | Fields |
| --- | --- |
| `@inproceedings` | `author`, `year`, `pages`, `title`, `booktitle`, `note` |
| `@article` | `author`, `journal`, `note`, `number`, `pages`, `title`, `volume`, `year` |
| `@book` | `author`, `year`, `publisher`, `title`, `note`, `edition` |

Anything that is genuinely none of those — a dataset, a model card, a
documentation page, an unpublished preprint — falls back to a `@misc` schema of
`author`, `year`, `title`, `note`.

Real output from the bibliography in this repo:

```bibtex
@inproceedings{panayotov2015librispeech,
  author    = {Vassil Panayotov and
               Guoguo Chen and
               Daniel Povey and
               Sanjeev Khudanpur},
  year      = {2015},
  pages     = {5206--5210},
  title     = {Librispeech: An {ASR} corpus based on public domain audio books},
  booktitle = {IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP)},
  note      = {\url{https://doi.org/10/gfv84w}}
}

@article{jacobs1991,
  author  = {Robert A. Jacobs and
             Michael I. Jordan and
             Steven J. Nowlan and
             Geoffrey E. Hinton},
  journal = {Neural Computation},
  note    = {\url{https://doi.org/10/cnsnqg}},
  number  = {1},
  pages   = {79--87},
  title   = {Adaptive Mixtures of Local Experts},
  volume  = {3},
  year    = {1991}
}
```

The rules behind that:

- **Authors** are separated by ` and `, one per line, with full given names.
  `Li, J.` becomes `Jinyu Li` when a database can supply the full name; entries
  where it can't are reported.
- **Venues** get their full name with the acronym in parentheses.
  `Proc. Int. Conf. Mach. Learn. (ICML)` and `Proceedings of the 23rd
  international conference on Machine learning` both become
  `International Conference on Machine Learning (ICML)`.
- **Links** always live in `note`, wrapped in `\url{}`, preferring a short DOI,
  then the full DOI, then a direct URL. An arXiv ID is resolved to the published
  DOI first, then shortened.
- **Missing fields** are either filled with `MISSING` or omitted, per config, and
  always listed in the report.

## Verification

Each entry is looked up in order, stopping at the first confident match:

1. **By DOI** — Crossref, then OpenAlex, then Semantic Scholar. If the DOI
   resolves to a work with a different title, that is reported and the search
   continues; a DOI pointing at the wrong paper is a finding in itself.
2. **By arXiv ID** — resolves the preprint, and follows through to the published
   DOI when arXiv records one.
3. **By title and author** — Crossref, DBLP, OpenAlex, Semantic Scholar, arXiv.

A candidate is scored on title similarity, author-surname overlap and year
proximity, giving one of three verdicts:

| Verdict | Meaning |
| --- | --- |
| `verified` | Confidently the same work. Its metadata is used to correct the entry. |
| `fuzzy` | Close, but not conclusive. Reported with the candidate; **your** metadata is kept. |
| `unverified` | No database recognised it. Possibly fabricated — check by hand. |

A `fuzzy` result never silently overwrites your entry; that is a deliberate
choice, because a wrong "correction" is worse than no correction.

Two details worth knowing:

- **Preprint vs published.** Databases hold both versions of most papers under
  the same title. Finding a preprint is treated as inconclusive and the search
  continues, so a paper that appeared at ICLR ends up as `@inproceedings` with
  the ICLR record, not as a `@misc` arXiv entry. Papers that really were never
  published stay `@misc`.
- **Truncated titles.** A citation that dropped the subtitle scores poorly on
  string similarity, so full word-containment plus matching authors and year is
  accepted as its own signal.

## Robustness

Network problems degrade the result; they never abort the run.

- Exponential backoff with jitter on 429, 5xx, timeouts and connection errors,
  honouring `Retry-After` when the server sends it.
- Per-host rate limiting, with slower defaults for the APIs that throttle
  hardest (arXiv 3s, DBLP 2s, Semantic Scholar 1.5s).
- Every response cached to `.bibformatter-cache.json`, flushed to disk as the
  run proceeds. An interrupted run loses nothing and the next one resumes from
  the cache — the first run over a large file takes a few minutes, re-runs take
  seconds.
- A provider that fails, returns nonsense, or exhausts its retries is skipped
  and the next one is tried.
- A circuit breaker drops a host that fails repeatedly, so one unreachable API
  can't stall every worker behind its retry backoff. The report names any host
  that was dropped, because "unverified" then may just mean nobody answered.

## Configuration

Everything is optional; `config.yaml` in this repo documents the full set with
defaults. Pass it with `-c config.yaml`. A config only needs to state what it
changes.

```yaml
missing:
  policy: omit               # or "placeholder" to write MISSING

schemas:                     # field sets, and the output field order
  article: [author, journal, note, number, pages, title, volume, year]

authors:
  name_order: last-first     # "Lovelace, Ada" instead of "Ada Lovelace"

venues:
  acronyms:                  # teach it venues the built-in table lacks
    "International Workshop on Widget Science": "IWWS"

verification:
  accept_threshold: 0.95     # stricter matching
  protected_fields: [note]   # never let a database overwrite your notes

network:
  mailto: you@example.com    # Crossref polite pool
```

## Reports

`--report report.json` writes the whole analysis as JSON — per-entry verdicts,
match scores, every correction applied, unresolved fields and duplicate keys —
for scripting or for diffing between runs.

`--check --strict` verifies without writing and exits non-zero if anything is
unverified, still `MISSING`, or has a duplicate citation key, which is what you
want in CI:

```yaml
- run: bib-formatter refs.bib --check --strict --mailto ${{ secrets.EMAIL }}
```

## Limitations

- Venue acronyms come from a built-in table covering common ML, speech and NLP
  venues, plus whatever is in your config. Unknown venues keep their name and
  any acronym they already carried, rather than getting an invented one.
- Web resources (model cards, documentation, dataset release notes) are not in
  any bibliographic database and will always report as `unverified`. That is
  correct behaviour, not a failure.
- Semantic Scholar rate-limits unauthenticated traffic aggressively. Set
  `SEMANTIC_SCHOLAR_API_KEY` if you have a key.

## Licence

MIT
