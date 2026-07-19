"""Configuration loading and defaults.

A user config (YAML or JSON) is deep-merged over DEFAULTS, so a config file only
needs to state what it changes.
"""

from __future__ import annotations

import json
import os
from typing import Any, Dict

DEFAULTS: Dict[str, Any] = {
    # --- what a finished entry must look like -------------------------------
    # Order matters: it is also the field order in the output file.
    "schemas": {
        "inproceedings": ["author", "year", "pages", "title", "booktitle", "note"],
        "article": [
            "author",
            "journal",
            "note",
            "number",
            "pages",
            "title",
            "volume",
            "year",
        ],
        "book": ["author", "year", "publisher", "title", "note", "edition"],
        # Fallback schema for entries that are genuinely not one of the three
        # publication types above (datasets, model cards, documentation).
        "misc": ["author", "year", "title", "note"],
    },
    # Entry types in the input that should be rewritten to a canonical type.
    # Anything resolved from a database overrides this mapping.
    "type_aliases": {
        "conference": "inproceedings",
        "proceedings": "inproceedings",
        "incollection": "inproceedings",
        "inbook": "inproceedings",
        "journal": "article",
        "online": "misc",
        "electronic": "misc",
        "techreport": "misc",
        "unpublished": "misc",
        "phdthesis": "misc",
        "mastersthesis": "misc",
    },
    # Entry types copied to the output byte-for-byte (BibTeX control entries).
    "passthrough_types": ["ieeetranbstctl", "preamble", "string", "comment"],
    # --- missing data --------------------------------------------------------
    "missing": {
        # "placeholder" -> write the placeholder string; "omit" -> drop the field.
        "policy": "placeholder",
        "placeholder": "MISSING",
        # Fields never worth a placeholder even in placeholder mode: an absent
        # edition means "1st edition", not "unknown".
        "never_placeholder": ["edition"],
        # Fields where a *verified* record having no value is taken as "this
        # does not exist" rather than "we could not find it".
        #
        # Many journals (IEEE TASLP, OJSP, SPL) publish continuously and
        # register no issue number, and ICLR/NeurIPS papers have no page range.
        # Writing `no.~MISSING` or `p.~MISSING` into a submitted manuscript is
        # a visible defect, so when the authoritative record is silent on these
        # the field is simply omitted. Entries we could not verify still get a
        # placeholder, because there the data really is unknown.
        "trust_verified_absence": ["number", "pages", "edition"],
    },
    # --- authors -------------------------------------------------------------
    "authors": {
        "separator": " and ",
        "one_per_line": True,
        # "first-last" -> "Ada Lovelace"; "last-first" -> "Lovelace, Ada"
        "name_order": "first-last",
        # Report entries still containing initials after all lookups.
        # (Corporate authors like {NVIDIA} are always kept braced and never split.)
        "require_full_names": True,
    },
    # --- venues --------------------------------------------------------------
    "venues": {
        # Rewrite to "Full Venue Name (ACRONYM)".
        "append_acronym": True,
        # Strip proceedings boilerplate before matching ("Proc. of the 5th ...").
        "strip_boilerplate": True,
        # Extra acronyms merged over the built-in table in venues.py.
        # "Some Very Specific Workshop": "SVSW"
        "acronyms": {},
    },
    # --- links ---------------------------------------------------------------
    "links": {
        # Order in which a link for the `note` field is chosen.
        "priority": ["short_doi", "doi", "url"],
        # If an arXiv id is present, resolve it to the published DOI first.
        "resolve_arxiv_to_doi": True,
        "wrapper": "\\url{{{url}}}",
        "doi_base": "https://doi.org/",
        # Keep any human-readable note text (e.g. "Accessed: Aug. 2025") and
        # append the URL after it, instead of replacing the note.
        "preserve_note_text": True,
    },
    # --- network -------------------------------------------------------------
    "network": {
        "enabled": True,
        "cache_path": ".bibformatter-cache.json",
        "cache_ttl_days": 90,
        "timeout": 30,
        # 3 retries with backoff rides out transient errors; beyond that a host
        # is genuinely unwell and the circuit breaker should take over quickly
        # rather than stalling every worker behind a long retry ladder.
        "max_retries": 3,
        "backoff_factor": 1.5,
        "backoff_max": 60.0,
        # Minimum seconds between requests to the same host.
        "min_interval": 0.25,
        # Per-host overrides. arXiv asks for one request every 3s; DBLP and
        # Semantic Scholar throttle aggressively and return 429 well before
        # their documented limits when unauthenticated.
        "host_intervals": {
            "export.arxiv.org": 3.0,
            "dblp.org": 2.0,
            "api.semanticscholar.org": 1.5,
            "shortdoi.org": 0.5,
        },
        # Consecutive failures before a host is skipped for the rest of the run.
        "circuit_threshold": 3,
        # Crossref's polite pool: set this to your email for better rate limits.
        "mailto": None,
        # Optional Semantic Scholar API key (env: SEMANTIC_SCHOLAR_API_KEY).
        "semantic_scholar_api_key": None,
    },
    # --- verification --------------------------------------------------------
    "verification": {
        "enabled": True,
        # Tried in order; the first confident match wins.
        "providers": ["crossref", "dblp", "openalex", "semanticscholar", "arxiv"],
        # Title similarity needed to accept a record as the same work.
        "accept_threshold": 0.90,
        # Below accept but above this -> reported as "fuzzy" for human review.
        "review_threshold": 0.70,
        # A year this far off still matches (preprint vs proceedings year).
        "year_tolerance": 1,
        # Overwrite local metadata with the verified record's metadata.
        "overwrite_fields": True,
        # Fields never taken from the remote record.
        "protected_fields": [],
    },
    # --- output --------------------------------------------------------------
    "output": {
        # Sort entries by citation key; "none" keeps input order.
        "sort_by": "key",
        "indent": "  ",
        # Pad the `=` so field values line up.
        "align_values": True,
        "entry_separator": "\n",
    },
}


class ConfigError(Exception):
    pass


def _deep_merge(base: Dict[str, Any], override: Dict[str, Any]) -> Dict[str, Any]:
    """Recursively merge `override` into `base`, returning a new dict."""
    out = dict(base)
    for key, value in override.items():
        if isinstance(value, dict) and isinstance(out.get(key), dict):
            out[key] = _deep_merge(out[key], value)
        else:
            out[key] = value
    return out


def load_config(path: str | None = None) -> Dict[str, Any]:
    """Load a YAML/JSON config and merge it over the defaults."""
    config = json.loads(json.dumps(DEFAULTS))  # deep copy

    if path:
        if not os.path.exists(path):
            raise ConfigError(f"config file not found: {path}")
        with open(path, "r", encoding="utf-8") as handle:
            text = handle.read()
        try:
            if path.endswith((".yaml", ".yml")):
                import yaml

                user = yaml.safe_load(text) or {}
            else:
                user = json.loads(text) if text.strip() else {}
        except Exception as exc:
            raise ConfigError(f"could not parse {path}: {exc}") from exc
        if not isinstance(user, dict):
            raise ConfigError(f"{path}: top level must be a mapping")
        config = _deep_merge(config, user)

    # Environment overrides for secrets, so they stay out of the config file.
    env_mail = os.environ.get("CROSSREF_MAILTO")
    if env_mail:
        config["network"]["mailto"] = env_mail
    env_key = os.environ.get("SEMANTIC_SCHOLAR_API_KEY")
    if env_key:
        config["network"]["semantic_scholar_api_key"] = env_key

    _validate(config)
    return config


def _validate(config: Dict[str, Any]) -> None:
    policy = config["missing"]["policy"]
    if policy not in ("placeholder", "omit"):
        raise ConfigError(f"missing.policy must be 'placeholder' or 'omit', got {policy!r}")

    order = config["authors"]["name_order"]
    if order not in ("first-last", "last-first"):
        raise ConfigError(
            f"authors.name_order must be 'first-last' or 'last-first', got {order!r}"
        )

    known_providers = {"crossref", "arxiv", "dblp", "openalex", "semanticscholar"}
    for provider in config["verification"]["providers"]:
        if provider not in known_providers:
            raise ConfigError(
                f"unknown provider {provider!r}; known: {sorted(known_providers)}"
            )

    accept = config["verification"]["accept_threshold"]
    review = config["verification"]["review_threshold"]
    if not 0 < review <= accept <= 1:
        raise ConfigError(
            "verification thresholds must satisfy 0 < review_threshold "
            "<= accept_threshold <= 1"
        )

    if not config["schemas"]:
        raise ConfigError("schemas must not be empty")
