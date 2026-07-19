"""Build schema-conformant entries from local data plus a verified record."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional, Tuple

from bibformatter import venues
from bibformatter.matching import token_containment
from bibformatter.normalize import (
    extract_arxiv_id,
    extract_doi,
    extract_url,
    format_name,
    is_corporate,
    latex_to_text,
    looks_like_initials,
    normalize_for_match,
    normalize_pages,
    normalize_year,
    parse_name,
    split_authors,
    text_to_latex,
)
from bibformatter.providers import BOOK, CONFERENCE, JOURNAL, MISC, PREPRINT, Record
from bibformatter.verify import VERIFIED, Verification

# Which BibTeX type each resolved publication kind becomes.
KIND_TO_TYPE = {
    JOURNAL: "article",
    CONFERENCE: "inproceedings",
    BOOK: "book",
    PREPRINT: "misc",
}

# Fields that hold the venue, per entry type.
VENUE_FIELD = {"article": "journal", "inproceedings": "booktitle"}


@dataclass
class LocalEntry:
    """The useful content of a raw parsed BibTeX entry."""

    key: str
    raw_type: str
    fields: Dict[str, str]
    title: str = ""
    authors: List[str] = field(default_factory=list)
    year: str = ""
    doi: Optional[str] = None
    arxiv_id: Optional[str] = None
    url: Optional[str] = None
    note_text: str = ""


@dataclass
class ProcessedEntry:
    key: str
    entry_type: str
    fields: Dict[str, str]
    verification: Verification
    local: LocalEntry
    missing: List[str] = field(default_factory=list)
    warnings: List[str] = field(default_factory=list)
    changes: List[str] = field(default_factory=list)
    passthrough: Optional[str] = None


def read_local(raw: Dict[str, str]) -> LocalEntry:
    """Pull the identifying metadata out of a raw parsed entry."""
    fields = {
        k.lower(): v for k, v in raw.items() if k not in ("ID", "ENTRYTYPE")
    }
    key = raw.get("ID", "")
    raw_type = (raw.get("ENTRYTYPE") or "misc").lower()

    note = fields.get("note", "")
    doi = extract_doi(
        fields.get("doi"), fields.get("url"), note, fields.get("howpublished")
    )
    arxiv_id = extract_arxiv_id(
        fields.get("eprint"),
        fields.get("url"),
        fields.get("journal"),
        note,
        fields.get("archiveprefix"),
    )
    # An "eprint" that is actually a PDF link is not an arXiv id.
    url = extract_url(fields.get("url"), note, fields.get("howpublished"))

    return LocalEntry(
        key=key,
        raw_type=raw_type,
        fields=fields,
        title=fields.get("title", ""),
        authors=split_authors(fields.get("author") or fields.get("editor") or ""),
        year=normalize_year(fields.get("year")),
        doi=doi,
        arxiv_id=arxiv_id,
        url=url,
        note_text=note,
    )


def decide_type(
    local: LocalEntry, verification: Verification, config: Dict[str, Any]
) -> Tuple[str, Optional[str]]:
    """Choose the output entry type. Returns (type, change_description)."""
    aliases = config["type_aliases"]
    schemas = config["schemas"]
    original = aliases.get(local.raw_type, local.raw_type)
    if original not in schemas:
        original = "misc" if "misc" in schemas else next(iter(schemas))

    if verification.status == VERIFIED and verification.record:
        kind = verification.record.kind
        resolved = KIND_TO_TYPE.get(kind)

        # For a venue we recognise, our own table outranks the database's type
        # field. Semantic Scholar reports NeurIPS papers as journal articles,
        # which would put a conference name in a `journal` field.
        venue_kind = None
        if verification.record.venue:
            _display, _acronym, venue_kind = venues.canonicalize(
                verification.record.venue,
                extra_acronyms=config["venues"].get("acronyms"),
                append_acronym=False,
            )
        if venue_kind == "conference":
            resolved, kind = "inproceedings", "conference"
        elif venue_kind == "journal":
            resolved, kind = "article", "journal"
        elif venue_kind == "preprint":
            # "Published in arXiv" is not a publication. Databases that call it
            # a journal would otherwise produce `journal = {arXiv}`.
            resolved, kind = KIND_TO_TYPE[PREPRINT], PREPRINT

        if resolved and resolved in schemas:
            if resolved != original:
                return resolved, (
                    f"type {local.raw_type} -> {resolved} "
                    f"({verification.record.source} says {kind})"
                )
            return resolved, None

    return original, (
        f"type {local.raw_type} -> {original}" if original != local.raw_type else None
    )


def build_authors(
    local: LocalEntry, verification: Verification, config: Dict[str, Any]
) -> Tuple[str, List[str]]:
    """Return (bibtex author value, warnings)."""
    settings = config["authors"]
    warnings: List[str] = []

    names = list(local.authors)
    if (
        verification.status == VERIFIED
        and verification.record
        and verification.record.authors
        and config["verification"]["overwrite_fields"]
        and "author" not in config["verification"]["protected_fields"]
    ):
        # Prefer the database's list, which is usually complete and unabbreviated.
        local_abbrev = sum(
            1 for n in names if looks_like_initials(parse_name(n)[0])
        )
        remote_abbrev = sum(
            1 for n in verification.record.authors
            if looks_like_initials(parse_name(n)[0])
        )
        local_has_etal = any("et al" in latex_to_text(n).lower() for n in names)
        if (
            not names
            or local_has_etal
            or remote_abbrev < local_abbrev
            or len(verification.record.authors) > len(names)
        ):
            names = verification.record.authors

    if not names:
        return "", ["no author found"]

    rendered: List[str] = []
    for name in names:
        if is_corporate(name):
            rendered.append(name.strip())
            continue
        given, family = parse_name(name)
        # Round-trip every name through Unicode so local and remote names end up
        # in the same LaTeX encoding, whichever source they came from.
        given = text_to_latex(latex_to_text(given))
        family = text_to_latex(latex_to_text(family))
        if settings["require_full_names"] and looks_like_initials(given):
            warnings.append(f"abbreviated author name: {format_name(given, family)}")
        if "et al" in latex_to_text(f"{given} {family}").lower():
            warnings.append("author list contains 'et al.'")
        rendered.append(format_name(given, family, settings["name_order"]))

    return settings["separator"].join(rendered), warnings


def build_venue(
    entry_type: str,
    local: LocalEntry,
    verification: Verification,
    config: Dict[str, Any],
) -> Tuple[str, Optional[str]]:
    """Return (venue value, change description) for journal/booktitle."""
    settings = config["venues"]
    field_name = VENUE_FIELD.get(entry_type)
    if not field_name:
        return "", None

    # Decode to plain text first: canonicalize and text_to_latex both expect it,
    # and a venue like "Cybernetics \& Informatics" would otherwise be escaped twice.
    original = latex_to_text(
        local.fields.get(field_name)
        or local.fields.get("booktitle" if field_name == "journal" else "journal")
        or ""
    )

    source = original
    if (
        verification.status == VERIFIED
        and verification.record
        and verification.record.venue
        and config["verification"]["overwrite_fields"]
        and field_name not in config["verification"]["protected_fields"]
    ):
        source = verification.record.venue

    display, acronym, _kind = venues.canonicalize(
        source,
        extra_acronyms=settings.get("acronyms"),
        append_acronym=settings["append_acronym"],
        do_strip=settings["strip_boilerplate"],
    )

    # If the database gave a bare acronym but the local entry had the full name
    # (or vice versa), keep whichever produced a real venue name.
    if not display and original:
        display, acronym, _kind = venues.canonicalize(
            original,
            extra_acronyms=settings.get("acronyms"),
            append_acronym=settings["append_acronym"],
            do_strip=settings["strip_boilerplate"],
        )

    if not display:
        return "", None

    value = text_to_latex(display, protect_caps=False)
    change = None
    if original and original != display:
        change = f"{field_name}: {original!r} -> {display!r}"
    return value, change


def build_note(
    local: LocalEntry,
    verification: Verification,
    config: Dict[str, Any],
    short_doi_resolver,
) -> Tuple[str, Optional[str]]:
    """Build the `note` field: prose (optional) plus the best link in \\url{}."""
    settings = config["links"]

    doi = local.doi
    url = local.url
    arxiv_id = local.arxiv_id
    # Only a verified record may contribute identifiers. A fuzzy candidate was
    # rejected as "probably a different paper", so taking its DOI would point
    # the citation at that other paper — the exact error we declined to make
    # when we refused to overwrite the other fields.
    if verification.status == VERIFIED and verification.record:
        doi = verification.record.doi or doi
        url = verification.record.url or url
        arxiv_id = verification.record.arxiv_id or arxiv_id

    # Rule 6 wants a link on every entry. Plenty of venues register no DOI
    # (ICLR, JMLR, older workshops), so an arXiv id we discovered along the way
    # is a better answer than MISSING.
    if not url and arxiv_id:
        url = f"https://arxiv.org/abs/{arxiv_id}"

    chosen: Optional[str] = None
    kind = None
    for preference in settings["priority"]:
        if preference == "short_doi" and doi:
            short = short_doi_resolver(doi)
            if short:
                chosen = settings["doi_base"] + short
                kind = "short DOI"
                break
        elif preference == "doi" and doi:
            chosen = settings["doi_base"] + doi
            kind = "DOI"
            break
        elif preference == "url" and url:
            chosen = url
            kind = "URL"
            break

    # Keep human-readable note prose ("Accessed: Aug. 2025"), drop old links.
    prose = ""
    if settings["preserve_note_text"] and local.note_text:
        prose = re.sub(r"\\url\{[^}]*\}", "", local.note_text)
        prose = re.sub(r"https?://\S+", "", prose)
        prose = re.sub(r"\[Online\]\.?\s*Available:?", "", prose, flags=re.I)
        prose = re.sub(r"\s+", " ", prose).strip(" .,;:").strip()

    if not chosen:
        return (prose, None) if prose else ("", None)

    link = settings["wrapper"].format(url=chosen)
    value = f"{prose}. {link}" if prose else link
    return value, f"note: {kind} link {chosen}"


# Fields that describe a work's position within a published volume. A preprint
# record has no meaningful values for these, and databases sometimes supply
# junk (DBLP's "abs/2101.03961" pseudo-volume), so they are never taken from one.
_PUBLICATION_ONLY_FIELDS = frozenset({"volume", "number", "pages"})


def _remote_value(
    name: str, verification: Verification, config: Dict[str, Any]
) -> str:
    if (
        verification.status != VERIFIED
        or not verification.record
        or not config["verification"]["overwrite_fields"]
        or name in config["verification"]["protected_fields"]
    ):
        return ""
    if (
        name in _PUBLICATION_ONLY_FIELDS
        and verification.record.kind in (PREPRINT, MISC)
    ):
        return ""
    return getattr(verification.record, name, "") or ""


def build_entry(
    local: LocalEntry,
    verification: Verification,
    config: Dict[str, Any],
    short_doi_resolver,
) -> ProcessedEntry:
    """Assemble the final, schema-conformant entry."""
    entry_type, type_change = decide_type(local, verification, config)
    schema = config["schemas"][entry_type]
    missing_cfg = config["missing"]

    result = ProcessedEntry(
        key=local.key,
        entry_type=entry_type,
        fields={},
        verification=verification,
        local=local,
    )
    if type_change:
        result.changes.append(type_change)

    author_value, author_warnings = build_authors(local, verification, config)
    result.warnings.extend(author_warnings)

    note_value, note_change = build_note(
        local, verification, config, short_doi_resolver
    )
    if note_change:
        result.changes.append(note_change)

    values: Dict[str, str] = {}

    for name in schema:
        if name == "author":
            values[name] = author_value

        elif name == "note":
            values[name] = note_value

        elif name == "title":
            remote_title = _remote_value("title", verification, config)
            local_title = latex_to_text(local.title)
            title = remote_title or local_title
            # Databases often store a title without its subtitle. When the
            # record's title is wholly contained in ours and ours is longer,
            # ours is the fuller form and replacing it would lose information.
            if remote_title and local_title:
                if (
                    token_containment(remote_title, local_title) >= 1.0
                    and len(normalize_for_match(local_title))
                    > len(normalize_for_match(remote_title))
                ):
                    title = local_title
            values[name] = text_to_latex(title, protect_caps=True)
            if (
                latex_to_text(local.title)
                and title
                and latex_to_text(local.title) != title
            ):
                result.changes.append(
                    f"title: {latex_to_text(local.title)!r} -> {title!r}"
                )

        elif name == "year":
            remote_year = _remote_value("year", verification, config)
            year = remote_year or local.year
            values[name] = year
            if remote_year and local.year and remote_year != local.year:
                result.changes.append(f"year: {local.year} -> {remote_year}")

        elif name in ("journal", "booktitle"):
            venue_value, venue_change = build_venue(
                entry_type, local, verification, config
            )
            values[name] = venue_value
            if venue_change:
                result.changes.append(venue_change)

        elif name == "pages":
            remote_pages = _remote_value("pages", verification, config)
            values[name] = normalize_pages(
                remote_pages or local.fields.get("pages", "")
            )

        elif name in ("volume", "number", "publisher", "edition"):
            remote = _remote_value(name, verification, config)
            local_value = latex_to_text(local.fields.get(name, ""))
            value = remote or local_value
            values[name] = text_to_latex(value) if value else ""
            if remote and local_value and remote != local_value:
                result.changes.append(f"{name}: {local_value!r} -> {remote!r}")

        else:
            values[name] = latex_to_text(local.fields.get(name, ""))

    # Apply the missing-data policy.
    never = set(missing_cfg.get("never_placeholder") or [])
    trusted_absence = set(missing_cfg.get("trust_verified_absence") or [])
    is_verified = verification.status == VERIFIED

    final: Dict[str, str] = {}
    for name in schema:
        value = (values.get(name) or "").strip()
        if value:
            final[name] = value
            continue
        if name in never:
            continue  # absent by convention, not a defect
        if name in trusted_absence and is_verified:
            # We asked the authority and it has no such value, so the field
            # genuinely does not exist for this work.
            continue
        result.missing.append(name)
        if missing_cfg["policy"] == "placeholder":
            final[name] = missing_cfg["placeholder"]

    result.fields = final
    return result


def validate(result: ProcessedEntry, config: Dict[str, Any]) -> List[str]:
    """Check a built entry against its schema. Returns a list of problems."""
    problems: List[str] = []
    schema = config["schemas"].get(result.entry_type)
    if schema is None:
        problems.append(f"no schema for type '{result.entry_type}'")
        return problems

    # A field is only "expected" if it could meaningfully be present: fields
    # absent by convention, and fields a verified record says don't exist, are
    # legitimately missing rather than schema violations.
    optional = set(config["missing"].get("never_placeholder") or [])
    if result.verification.status == VERIFIED:
        optional |= set(config["missing"].get("trust_verified_absence") or [])
    expected = [f for f in schema if f not in optional or f in result.fields]

    unexpected = [f for f in result.fields if f not in schema]
    if unexpected:
        problems.append(f"unexpected fields: {', '.join(sorted(unexpected))}")

    if config["missing"]["policy"] == "placeholder":
        absent = [f for f in expected if f not in result.fields]
        if absent:
            problems.append(f"fields absent: {', '.join(absent)}")
        if len(result.fields) != len(expected):
            problems.append(
                f"expected {len(expected)} fields, got {len(result.fields)}"
            )

    placeholder = config["missing"]["placeholder"]
    unresolved = [k for k, v in result.fields.items() if v == placeholder]
    if unresolved:
        problems.append(f"unresolved {placeholder}: {', '.join(sorted(unresolved))}")

    # A note without a link means rule 6 was not satisfiable.
    note = result.fields.get("note", "")
    if "note" in schema and note and note != placeholder and "\\url{" not in note:
        problems.append("note has no \\url{} link")

    return problems
