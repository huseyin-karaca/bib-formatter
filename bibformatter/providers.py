"""Bibliographic database clients.

Every provider maps its API's response onto the same `Record`, so the
verification logic never has to care where a record came from. Providers return
None (never raise) when they can't answer, letting the caller fall through to
the next database in the chain.
"""

from __future__ import annotations

import logging
import re
import xml.etree.ElementTree as ET
from dataclasses import dataclass, field
from typing import Any, Dict, List, Optional

from bibformatter.net import HttpClient
from bibformatter.normalize import normalize_pages, normalize_year

log = logging.getLogger(__name__)

# Publication kinds we normalize onto.
JOURNAL, CONFERENCE, BOOK, PREPRINT, MISC = (
    "journal",
    "conference",
    "book",
    "preprint",
    "misc",
)


@dataclass
class Record:
    """A normalized bibliographic record from any database."""

    source: str
    title: str = ""
    authors: List[str] = field(default_factory=list)  # "Given Family", Unicode
    year: str = ""
    venue: str = ""
    kind: str = MISC
    volume: str = ""
    number: str = ""
    pages: str = ""
    publisher: str = ""
    edition: str = ""
    doi: str = ""
    url: str = ""
    arxiv_id: str = ""

    def is_empty(self) -> bool:
        return not self.title and not self.doi


def _clean(value: Any) -> str:
    if value is None:
        return ""
    return re.sub(r"\s+", " ", str(value)).strip()


def _first(value: Any) -> str:
    """Crossref and friends love single-element lists."""
    if isinstance(value, list):
        return _clean(value[0]) if value else ""
    return _clean(value)


# Databases disagree about preprints: Semantic Scholar calls arXiv a journal,
# OpenAlex gives it a "source", DBLP files it under CoRR. Whatever the claimed
# type, a work whose venue is a preprint server is a preprint.
_PREPRINT_VENUES = re.compile(
    r"^(arxiv|corr|biorxiv|medrxiv|ssrn|openreview|research\s*square|techrxiv)\b",
    re.I,
)


def _fix_preprint_kind(record: Record) -> Record:
    """Downgrade a record to `preprint` when its venue is a preprint server."""
    if record.venue and _PREPRINT_VENUES.match(record.venue.strip()):
        record.kind = PREPRINT
    return record


# ---------------------------------------------------------------------------
# Crossref
# ---------------------------------------------------------------------------

CROSSREF_TYPE_MAP = {
    "journal-article": JOURNAL,
    "proceedings-article": CONFERENCE,
    "book": BOOK,
    "monograph": BOOK,
    "reference-book": BOOK,
    "edited-book": BOOK,
    "book-chapter": CONFERENCE,
    "book-section": CONFERENCE,
    "posted-content": PREPRINT,
    "report": MISC,
    "dataset": MISC,
}


class CrossrefProvider:
    name = "crossref"
    BASE = "https://api.crossref.org/works"

    def __init__(self, http: HttpClient):
        self.http = http

    def _params(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(extra)
        if self.http.mailto:
            params["mailto"] = self.http.mailto
        return params

    def by_doi(self, doi: str) -> Optional[Record]:
        if not doi:
            return None
        payload = self.http.get_json(f"{self.BASE}/{doi}", params=self._params({}))
        if not isinstance(payload, dict):
            return None
        message = payload.get("message")
        if not isinstance(message, dict):
            return None
        return self._to_record(message)

    def search(self, title: str, authors: List[str], year: str) -> List[Record]:
        if not title:
            return []
        query = title
        if authors:
            query = f"{title} {authors[0]}"
        params = self._params({"query.bibliographic": query, "rows": 5})
        payload = self.http.get_json(self.BASE, params=params)
        if not isinstance(payload, dict):
            return []
        items = payload.get("message", {}).get("items") or []
        records = []
        for item in items:
            record = self._to_record(item)
            if record:
                records.append(record)
        return records

    def _to_record(self, item: Dict[str, Any]) -> Optional[Record]:
        title = _first(item.get("title"))
        if not title:
            return None

        authors = []
        for person in item.get("author") or []:
            if person.get("name"):  # organisation
                authors.append(_clean(person["name"]))
                continue
            given = _clean(person.get("given"))
            family = _clean(person.get("family"))
            full = f"{given} {family}".strip()
            if full:
                authors.append(full)

        year = ""
        for key in ("published-print", "published-online", "issued", "created"):
            parts = (item.get(key) or {}).get("date-parts") or []
            if parts and parts[0] and parts[0][0]:
                year = normalize_year(parts[0][0])
                if year:
                    break

        kind = CROSSREF_TYPE_MAP.get(_clean(item.get("type")), MISC)
        venue = _first(item.get("container-title"))
        if kind == BOOK and not venue:
            venue = ""

        return _fix_preprint_kind(Record(
            source=self.name,
            title=title,
            authors=authors,
            year=year,
            venue=venue,
            kind=kind,
            volume=_clean(item.get("volume")),
            number=_clean(item.get("issue")),
            pages=normalize_pages(_clean(item.get("page"))),
            publisher=_clean(item.get("publisher")),
            edition=_clean(item.get("edition-number")),
            doi=_clean(item.get("DOI")),
            url=_clean(item.get("URL")),
        ))


# ---------------------------------------------------------------------------
# arXiv
# ---------------------------------------------------------------------------

_ATOM = "{http://www.w3.org/2005/Atom}"
_ARXIV_NS = "{http://arxiv.org/schemas/atom}"


class ArxivProvider:
    name = "arxiv"
    BASE = "http://export.arxiv.org/api/query"

    def __init__(self, http: HttpClient):
        self.http = http

    def by_id(self, arxiv_id: str) -> Optional[Record]:
        if not arxiv_id:
            return None
        text = self.http.get_text(self.BASE, params={"id_list": arxiv_id, "max_results": 1})
        records = self._parse(text)
        return records[0] if records else None

    def search(self, title: str, authors: List[str], year: str) -> List[Record]:
        if not title:
            return []
        # arXiv's query language wants field prefixes; quote the title phrase.
        safe_title = re.sub(r'["\\]', " ", title)
        text = self.http.get_text(
            self.BASE, params={"search_query": f'ti:"{safe_title}"', "max_results": 5}
        )
        return self._parse(text)

    def _parse(self, text: Optional[str]) -> List[Record]:
        if not text:
            return []
        try:
            root = ET.fromstring(text)
        except ET.ParseError as exc:
            log.debug("arXiv returned unparseable XML: %s", exc)
            return []

        records = []
        for entry in root.findall(f"{_ATOM}entry"):
            title = _clean((entry.findtext(f"{_ATOM}title") or ""))
            if not title:
                continue
            authors = [
                _clean(node.findtext(f"{_ATOM}name"))
                for node in entry.findall(f"{_ATOM}author")
            ]
            published = entry.findtext(f"{_ATOM}published") or ""
            doi = _clean(entry.findtext(f"{_ARXIV_NS}doi"))
            journal_ref = _clean(entry.findtext(f"{_ARXIV_NS}journal_ref"))

            arxiv_id = ""
            raw_id = _clean(entry.findtext(f"{_ATOM}id"))
            match = re.search(r"arxiv\.org/abs/(.+?)(?:v\d+)?$", raw_id, re.I)
            if match:
                arxiv_id = match.group(1)

            records.append(
                Record(
                    source=self.name,
                    title=title,
                    authors=[a for a in authors if a],
                    year=normalize_year(published),
                    venue=journal_ref,
                    # A published DOI means it is no longer just a preprint.
                    kind=PREPRINT if not doi else MISC,
                    doi=doi,
                    url=f"https://arxiv.org/abs/{arxiv_id}" if arxiv_id else raw_id,
                    arxiv_id=arxiv_id,
                )
            )
        return records


# ---------------------------------------------------------------------------
# DBLP
# ---------------------------------------------------------------------------

DBLP_TYPE_MAP = {
    "Conference and Workshop Papers": CONFERENCE,
    "Journal Articles": JOURNAL,
    "Books and Theses": BOOK,
    "Parts in Books or Collections": CONFERENCE,
    "Informal and Other Publications": PREPRINT,
    "Editorship": BOOK,
}


class DblpProvider:
    name = "dblp"
    BASE = "https://dblp.org/search/publ/api"

    def __init__(self, http: HttpClient):
        self.http = http

    def search(self, title: str, authors: List[str], year: str) -> List[Record]:
        if not title:
            return []
        payload = self.http.get_json(
            self.BASE, params={"q": title, "format": "json", "h": 5}
        )
        if not isinstance(payload, dict):
            return []
        hits = (payload.get("result", {}).get("hits", {}) or {}).get("hit") or []
        if isinstance(hits, dict):
            hits = [hits]

        records = []
        for hit in hits:
            info = hit.get("info") if isinstance(hit, dict) else None
            if not isinstance(info, dict):
                continue
            record = self._to_record(info)
            if record:
                records.append(record)
        return records

    def _to_record(self, info: Dict[str, Any]) -> Optional[Record]:
        title = _clean(info.get("title")).rstrip(".")
        if not title:
            return None

        raw_authors = (info.get("authors") or {}).get("author") or []
        if isinstance(raw_authors, dict):
            raw_authors = [raw_authors]
        authors = []
        for person in raw_authors:
            name = person.get("text") if isinstance(person, dict) else person
            name = _clean(name)
            # DBLP disambiguates duplicates with a trailing number: "Wei Li 0001".
            name = re.sub(r"\s+\d{4}$", "", name)
            if name:
                authors.append(name)

        return _fix_preprint_kind(Record(
            source=self.name,
            title=title,
            authors=authors,
            year=normalize_year(info.get("year")),
            venue=_clean(info.get("venue")),
            kind=DBLP_TYPE_MAP.get(_clean(info.get("type")), MISC),
            volume=_clean(info.get("volume")),
            number=_clean(info.get("number")),
            pages=normalize_pages(_clean(info.get("pages"))),
            publisher=_clean(info.get("publisher")),
            doi=_clean(info.get("doi")),
            url=_clean(info.get("ee")) or _clean(info.get("url")),
        ))


# ---------------------------------------------------------------------------
# OpenAlex
# ---------------------------------------------------------------------------

OPENALEX_TYPE_MAP = {
    "article": JOURNAL,
    "journal-article": JOURNAL,
    "proceedings-article": CONFERENCE,
    "book": BOOK,
    "book-chapter": CONFERENCE,
    "preprint": PREPRINT,
    "report": MISC,
    "dataset": MISC,
}


class OpenAlexProvider:
    name = "openalex"
    BASE = "https://api.openalex.org/works"

    def __init__(self, http: HttpClient):
        self.http = http

    def _params(self, extra: Dict[str, Any]) -> Dict[str, Any]:
        params = dict(extra)
        if self.http.mailto:
            params["mailto"] = self.http.mailto
        return params

    def by_doi(self, doi: str) -> Optional[Record]:
        if not doi:
            return None
        payload = self.http.get_json(f"{self.BASE}/doi:{doi}", params=self._params({}))
        if not isinstance(payload, dict) or not payload.get("id"):
            return None
        return self._to_record(payload)

    def search(self, title: str, authors: List[str], year: str) -> List[Record]:
        if not title:
            return []
        # Commas and colons break OpenAlex's filter syntax.
        safe = re.sub(r"[,:|]", " ", title)
        params = self._params({"filter": f"title.search:{safe}", "per-page": 5})
        payload = self.http.get_json(self.BASE, params=params)
        if not isinstance(payload, dict):
            return []
        records = []
        for item in payload.get("results") or []:
            record = self._to_record(item)
            if record:
                records.append(record)
        return records

    def _to_record(self, item: Dict[str, Any]) -> Optional[Record]:
        title = _clean(item.get("display_name") or item.get("title"))
        if not title:
            return None

        authors = []
        for authorship in item.get("authorships") or []:
            name = _clean((authorship.get("author") or {}).get("display_name"))
            if name:
                authors.append(name)

        location = item.get("primary_location") or {}
        source = location.get("source") or {}
        venue = _clean(source.get("display_name"))
        publisher = _clean(source.get("host_organization_name"))

        biblio = item.get("biblio") or {}
        first_page = _clean(biblio.get("first_page"))
        last_page = _clean(biblio.get("last_page"))
        pages = ""
        if first_page:
            pages = normalize_pages(f"{first_page}-{last_page}" if last_page else first_page)

        doi = _clean(item.get("doi"))
        doi = re.sub(r"^https?://(dx\.)?doi\.org/", "", doi, flags=re.I)

        kind = OPENALEX_TYPE_MAP.get(_clean(item.get("type")), MISC)
        if kind == JOURNAL and _clean(source.get("type")) == "conference":
            kind = CONFERENCE

        return _fix_preprint_kind(Record(
            source=self.name,
            title=title,
            authors=authors,
            year=normalize_year(item.get("publication_year")),
            venue=venue,
            kind=kind,
            volume=_clean(biblio.get("volume")),
            number=_clean(biblio.get("issue")),
            pages=pages,
            publisher=publisher,
            doi=doi,
            url=_clean(location.get("landing_page_url")),
        ))


# ---------------------------------------------------------------------------
# Semantic Scholar
# ---------------------------------------------------------------------------

S2_TYPE_MAP = {
    "JournalArticle": JOURNAL,
    "Conference": CONFERENCE,
    "Book": BOOK,
    "BookSection": CONFERENCE,
    "Review": JOURNAL,
}

S2_FIELDS = (
    "title,authors,year,venue,publicationTypes,publicationVenue,journal,externalIds"
)


class SemanticScholarProvider:
    name = "semanticscholar"
    BASE = "https://api.semanticscholar.org/graph/v1"

    def __init__(self, http: HttpClient, api_key: Optional[str] = None):
        self.http = http
        self.headers = {"x-api-key": api_key} if api_key else None

    def by_doi(self, doi: str) -> Optional[Record]:
        if not doi:
            return None
        payload = self.http.get_json(
            f"{self.BASE}/paper/DOI:{doi}", params={"fields": S2_FIELDS},
            headers=self.headers,
        )
        if not isinstance(payload, dict):
            return None
        return self._to_record(payload)

    def search(self, title: str, authors: List[str], year: str) -> List[Record]:
        if not title:
            return []
        payload = self.http.get_json(
            f"{self.BASE}/paper/search",
            params={"query": title, "limit": 5, "fields": S2_FIELDS},
            headers=self.headers,
        )
        if not isinstance(payload, dict):
            return []
        records = []
        for item in payload.get("data") or []:
            record = self._to_record(item)
            if record:
                records.append(record)
        return records

    def _to_record(self, item: Dict[str, Any]) -> Optional[Record]:
        title = _clean(item.get("title"))
        if not title:
            return None

        authors = [
            _clean(person.get("name"))
            for person in item.get("authors") or []
            if _clean(person.get("name"))
        ]

        kind = MISC
        for publication_type in item.get("publicationTypes") or []:
            if publication_type in S2_TYPE_MAP:
                kind = S2_TYPE_MAP[publication_type]
                break

        journal = item.get("journal") or {}
        venue = _clean(item.get("venue")) or _clean(
            (item.get("publicationVenue") or {}).get("name")
        )

        external = item.get("externalIds") or {}
        return _fix_preprint_kind(Record(
            source=self.name,
            title=title,
            authors=authors,
            year=normalize_year(item.get("year")),
            venue=venue,
            kind=kind,
            volume=_clean(journal.get("volume")),
            pages=normalize_pages(_clean(journal.get("pages"))),
            doi=_clean(external.get("DOI")),
            arxiv_id=_clean(external.get("ArXiv")),
        ))


# ---------------------------------------------------------------------------
# shortdoi.org
# ---------------------------------------------------------------------------


class ShortDoiProvider:
    """Resolves a DOI to its 10/xxxx short form."""

    name = "shortdoi"
    BASE = "https://shortdoi.org"

    def __init__(self, http: HttpClient):
        self.http = http

    def shorten(self, doi: str) -> Optional[str]:
        if not doi or doi.startswith("10/"):
            return doi or None
        payload = self.http.get_json(f"{self.BASE}/{doi}", params={"format": "json"})
        if not isinstance(payload, dict):
            return None
        short = _clean(payload.get("ShortDOI"))
        # The service answers "10/abcde"; treat anything else as a failure.
        return short if short.startswith("10/") else None


def build_providers(config: Dict[str, Any], http: HttpClient) -> Dict[str, Any]:
    """Instantiate the providers named in the config, keyed by name."""
    api_key = config["network"].get("semantic_scholar_api_key")
    available = {
        "crossref": lambda: CrossrefProvider(http),
        "arxiv": lambda: ArxivProvider(http),
        "dblp": lambda: DblpProvider(http),
        "openalex": lambda: OpenAlexProvider(http),
        "semanticscholar": lambda: SemanticScholarProvider(http, api_key),
    }
    return {
        name: available[name]()
        for name in config["verification"]["providers"]
        if name in available
    }
