import pytest

from bibformatter.config import ConfigError, load_config
from bibformatter.providers import BOOK, CONFERENCE, JOURNAL, PREPRINT, Record
from bibformatter.schema import build_entry, read_local, validate
from bibformatter.verify import UNVERIFIED, VERIFIED, Verification
from bibformatter.writer import format_entry


@pytest.fixture
def config():
    return load_config()


def no_short_doi(_doi):
    """Stand-in for the shortdoi service, so tests never touch the network."""
    return None


def verified(record):
    return Verification(status=VERIFIED, record=record, source=record.source)


def unverified():
    return Verification(status=UNVERIFIED)


def build(raw, config, verification=None, resolver=no_short_doi):
    local = read_local(raw)
    return build_entry(local, verification or unverified(), config, resolver)


class TestFieldSchemas:
    def test_inproceedings_has_exactly_six_fields(self, config):
        result = build(
            {
                "ID": "x", "ENTRYTYPE": "inproceedings",
                "author": "Ada Lovelace", "title": "A Paper", "year": "2020",
                "booktitle": "International Conference on Machine Learning",
                "pages": "1-10", "doi": "10.1234/x",
                "abstract": "should be dropped", "keywords": "also dropped",
            },
            config,
        )
        assert result.entry_type == "inproceedings"
        assert list(result.fields) == [
            "author", "year", "pages", "title", "booktitle", "note"
        ]

    def test_article_has_exactly_eight_fields(self, config):
        result = build(
            {
                "ID": "x", "ENTRYTYPE": "article",
                "author": "Ada Lovelace", "title": "A Paper", "year": "2020",
                "journal": "Neural Computation", "volume": "3", "number": "1",
                "pages": "79-87", "doi": "10.1234/x", "issn": "dropped",
            },
            config,
        )
        assert list(result.fields) == [
            "author", "journal", "note", "number", "pages", "title", "volume", "year"
        ]

    def test_book_has_six_fields_minus_absent_edition(self, config):
        result = build(
            {
                "ID": "x", "ENTRYTYPE": "book",
                "author": "Ada Lovelace", "title": "A Book", "year": "2020",
                "publisher": "MIT Press", "doi": "10.1234/x",
            },
            config,
        )
        # `edition` is in never_placeholder: absent means 1st, not unknown.
        assert list(result.fields) == ["author", "year", "publisher", "title", "note"]

    def test_book_keeps_edition_when_present(self, config):
        result = build(
            {
                "ID": "x", "ENTRYTYPE": "book", "author": "A B", "title": "T",
                "year": "2013", "publisher": "Wiley", "edition": "1st",
            },
            config,
        )
        assert result.fields["edition"] == "1st"

    def test_extra_fields_are_dropped(self, config):
        result = build(
            {
                "ID": "x", "ENTRYTYPE": "article", "author": "A B", "title": "T",
                "year": "2020", "journal": "J", "abstract": "long text",
                "keywords": "a;b", "isbn": "123", "month": "mar", "url": "http://x",
            },
            config,
        )
        for dropped in ("abstract", "keywords", "isbn", "month", "url"):
            assert dropped not in result.fields


class TestMissingPolicy:
    def test_placeholder_fills_gaps(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "A B", "title": "T",
             "year": "2020"},
            config,
        )
        assert result.fields["journal"] == "MISSING"
        assert result.fields["volume"] == "MISSING"
        assert set(result.missing) >= {"journal", "volume", "number", "pages"}

    def test_omit_drops_gaps(self, config):
        config["missing"]["policy"] = "omit"
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "A B", "title": "T",
             "year": "2020"},
            config,
        )
        assert "journal" not in result.fields
        assert "MISSING" not in result.fields.values()
        # Still reported, even though the field was omitted.
        assert "journal" in result.missing

    def test_custom_placeholder(self, config):
        config["missing"]["placeholder"] = "TODO"
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "A B", "title": "T",
             "year": "2020"},
            config,
        )
        assert result.fields["journal"] == "TODO"


class TestAuthors:
    def test_separated_by_and(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "Ada Lovelace and Alan Turing",
             "title": "T", "year": "2020", "publisher": "P"},
            config,
        )
        assert result.fields["author"] == "Ada Lovelace and Alan Turing"

    def test_converted_to_first_last(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "Lovelace, Ada and Turing, Alan",
             "title": "T", "year": "2020", "publisher": "P"},
            config,
        )
        assert result.fields["author"] == "Ada Lovelace and Alan Turing"

    def test_initials_are_reported(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "Li, J. and Deng, L.",
             "title": "T", "year": "2020", "publisher": "P"},
            config,
        )
        assert any("abbreviated" in w for w in result.warnings)

    def test_corporate_author_is_not_split(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "misc", "author": "{Mozilla Foundation}",
             "title": "T", "year": "2025"},
            config,
        )
        assert result.fields["author"] == "{Mozilla Foundation}"

    def test_verified_record_replaces_initials_with_full_names(self, config):
        record = Record(
            source="crossref", title="T", year="2020", kind=BOOK,
            authors=["Jinyu Li", "Li Deng"], publisher="P",
        )
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "Li, J. and Deng, L.",
             "title": "T", "year": "2020", "publisher": "P"},
            config, verified(record),
        )
        assert result.fields["author"] == "Jinyu Li and Li Deng"

    def test_et_al_is_replaced_and_reported(self, config):
        record = Record(
            source="dblp", title="T", year="2019", kind=CONFERENCE,
            authors=["Adam Paszke", "Sam Gross", "Francisco Massa"], venue="NeurIPS",
        )
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings",
             "author": "Paszke, Adam and et al.", "title": "T", "year": "2019"},
            config, verified(record),
        )
        assert "et al" not in result.fields["author"]
        assert len(result.fields["author"].split(" and ")) == 3


class TestVenues:
    def test_acronym_appended(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "A B", "title": "T",
             "year": "2021",
             "booktitle": "International Conference on Learning Representations"},
            config,
        )
        assert result.fields["booktitle"] == (
            "International Conference on Learning Representations (ICLR)"
        )

    def test_abbreviated_venue_expanded(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "A B", "title": "T",
             "year": "2021", "booktitle": "Proc. Int. Conf. Mach. Learn. (ICML)"},
            config,
        )
        assert result.fields["booktitle"] == (
            "International Conference on Machine Learning (ICML)"
        )

    def test_arxiv_is_not_accepted_as_a_journal(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "A B", "title": "T",
             "year": "2020", "journal": "arXiv preprint arXiv:1912.06670"},
            config,
        )
        assert result.fields["journal"] == "MISSING"

    def test_ampersand_venue_is_not_double_escaped(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "A B", "title": "T",
             "year": "2020", "journal": r"Cybernetics \& Informatics"},
            config,
        )
        assert "textbackslash" not in result.fields["journal"]
        assert r"\&" in result.fields["journal"]


class TestNote:
    def test_doi_is_wrapped_in_url(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "A B", "title": "T",
             "year": "2020", "publisher": "P", "doi": "10.1017/CBO9780511546921"},
            config,
        )
        assert result.fields["note"] == (
            r"\url{https://doi.org/10.1017/CBO9780511546921}"
        )

    def test_short_doi_preferred_over_doi(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "A B", "title": "T",
             "year": "2020", "publisher": "P", "doi": "10.1017/CBO9780511546921"},
            config, resolver=lambda _d: "10/abcde",
        )
        assert result.fields["note"] == r"\url{https://doi.org/10/abcde}"

    def test_url_used_when_no_doi(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "misc", "author": "A B", "title": "T",
             "year": "2025", "url": "https://example.org/page"},
            config,
        )
        assert result.fields["note"] == r"\url{https://example.org/page}"

    def test_note_prose_is_kept_alongside_the_link(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "misc", "author": "{NVIDIA}", "title": "T",
             "year": "2025",
             "note": r"[Online]. Available: \url{https://example.org/old} "
                     r"(accessed: Aug. 2025)",
             "url": "https://example.org/page"},
            config,
        )
        assert "accessed: Aug. 2025" in result.fields["note"]
        assert result.fields["note"].count(r"\url{") == 1
        assert "example.org/old" not in result.fields["note"]

    def test_missing_when_no_link_at_all(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "A B", "title": "T",
             "year": "2020", "publisher": "P"},
            config,
        )
        assert result.fields["note"] == "MISSING"


class TestTypeCorrection:
    def test_misc_promoted_to_inproceedings_when_verified(self, config):
        record = Record(source="dblp", title="T", year="2017", kind=CONFERENCE,
                        venue="NeurIPS", authors=["Ada Lovelace"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "misc", "author": "Ada Lovelace", "title": "T",
             "year": "2017", "eprint": "1706.03762"},
            config, verified(record),
        )
        assert result.entry_type == "inproceedings"
        assert "(NeurIPS)" in result.fields["booktitle"]

    def test_inbook_maps_to_inproceedings(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "inbook", "author": "A B", "title": "T",
             "year": "2019", "booktitle": "Advances in Neural Information Processing Systems"},
            config,
        )
        assert result.entry_type == "inproceedings"

    def test_online_maps_to_misc(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "online", "author": "{PyTorch}", "title": "T",
             "year": "2025", "url": "https://example.org"},
            config,
        )
        assert result.entry_type == "misc"

    def test_unpublished_preprint_stays_misc(self, config):
        record = Record(source="arxiv", title="T", year="2016", kind=PREPRINT,
                        authors=["Ada Lovelace"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "misc", "author": "Ada Lovelace", "title": "T",
             "year": "2016"},
            config, verified(record),
        )
        assert result.entry_type == "misc"

    def test_known_conference_venue_overrides_a_wrong_database_type(self, config):
        # Semantic Scholar reports NeurIPS papers as JournalArticle. Trusting it
        # would put a conference name in a `journal` field.
        record = Record(source="semanticscholar", title="Attention Is All You Need",
                        year="2017", kind=JOURNAL, venue="NeurIPS",
                        authors=["Ashish Vaswani"], pages="5998--6008")
        result = build(
            {"ID": "x", "ENTRYTYPE": "misc", "author": "Ashish Vaswani",
             "title": "Attention Is All You Need", "year": "2017"},
            config, verified(record),
        )
        assert result.entry_type == "inproceedings"
        assert "booktitle" in result.fields
        assert "journal" not in result.fields
        assert result.fields["booktitle"] == (
            "Conference on Neural Information Processing Systems (NeurIPS)"
        )

    def test_known_journal_venue_overrides_a_wrong_database_type(self, config):
        record = Record(source="dblp", title="T", year="2014", kind=CONFERENCE,
                        venue="IEEE/ACM Transactions on Audio, Speech, and Language Processing",
                        authors=["Ada Lovelace"], volume="22")
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Ada Lovelace",
             "title": "T", "year": "2014"},
            config, verified(record),
        )
        assert result.entry_type == "article"
        assert "journal" in result.fields

    def test_arxiv_venue_does_not_promote_a_preprint(self, config):
        record = Record(source="semanticscholar", title="Deep Speech", year="2014",
                        kind=JOURNAL, venue="arXiv.org", authors=["Awni Hannun"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "misc", "author": "Awni Hannun",
             "title": "Deep Speech", "year": "2014"},
            config, verified(record),
        )
        assert result.entry_type == "misc"

    def test_journal_record_makes_it_an_article(self, config):
        record = Record(source="crossref", title="T", year="2014", kind=JOURNAL,
                        venue="Neural Computation", volume="22", number="4",
                        pages="745--777", authors=["Ada Lovelace"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Ada Lovelace",
             "title": "T", "year": "2014"},
            config, verified(record),
        )
        assert result.entry_type == "article"
        assert result.fields["volume"] == "22"


class TestCorrections:
    def test_wrong_year_is_fixed_and_recorded(self, config):
        record = Record(source="dblp", title="T", year="2019", kind=CONFERENCE,
                        venue="ICML", authors=["Ada Lovelace"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Ada Lovelace",
             "title": "T", "year": "2018"},
            config, verified(record),
        )
        assert result.fields["year"] == "2019"
        assert any("year: 2018 -> 2019" in c for c in result.changes)

    def test_protected_field_is_left_alone(self, config):
        config["verification"]["protected_fields"] = ["author"]
        record = Record(source="crossref", title="T", year="2020", kind=BOOK,
                        authors=["Somebody Else"], publisher="P")
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "Ada Lovelace", "title": "T",
             "year": "2020", "publisher": "P"},
            config, verified(record),
        )
        assert result.fields["author"] == "Ada Lovelace"

    def test_overwrite_can_be_disabled(self, config):
        config["verification"]["overwrite_fields"] = False
        record = Record(source="dblp", title="T", year="2019", kind=CONFERENCE,
                        venue="ICML", authors=["Ada Lovelace"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Ada Lovelace",
             "title": "T", "year": "2018"},
            config, verified(record),
        )
        assert result.fields["year"] == "2018"


class TestValidate:
    def test_conformant_entry_has_no_problems(self, config):
        record = Record(source="crossref", title="T", year="2014", kind=JOURNAL,
                        venue="Neural Computation", volume="22", number="4",
                        pages="745--777", authors=["Ada Lovelace"],
                        doi="10.1234/x")
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "Ada Lovelace",
             "title": "T", "year": "2014", "doi": "10.1234/x"},
            config, verified(record),
        )
        assert validate(result, config) == []

    def test_reports_unresolved_placeholders(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "A B", "title": "T",
             "year": "2020"},
            config,
        )
        problems = validate(result, config)
        assert any("unresolved MISSING" in p for p in problems)


class TestWriter:
    def test_authors_are_one_per_line(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "Ada Lovelace and Alan Turing",
             "title": "T", "year": "2020", "publisher": "P"},
            config,
        )
        text = format_entry(result, config)
        assert "Ada Lovelace and\n" in text
        # Continuation aligns under the first author.
        author_line = [l for l in text.splitlines() if "Ada Lovelace" in l][0]
        turing_line = [l for l in text.splitlines() if "Alan Turing" in l][0]
        assert author_line.index("Ada") == turing_line.index("Alan")

    def test_field_order_matches_schema(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "A B", "title": "T",
             "year": "2020", "booktitle": "ICML", "pages": "1-9", "doi": "10.1/x"},
            config,
        )
        text = format_entry(result, config)
        order = [l.split("=")[0].strip() for l in text.splitlines()[1:-1]]
        assert order == ["author", "year", "pages", "title", "booktitle", "note"]

    def test_output_reparses_cleanly(self, config, tmp_path):
        import bibtexparser

        result = build(
            {"ID": "x", "ENTRYTYPE": "book", "author": "Nicolò Cesa-Bianchi",
             "title": "Prediction, Learning, and Games", "year": "2006",
             "publisher": "Cambridge University Press", "doi": "10.1017/CBO9780511546921"},
            config,
        )
        text = format_entry(result, config)
        reparsed = bibtexparser.loads(text).entries
        assert len(reparsed) == 1
        assert reparsed[0]["ID"] == "x"


class TestConfigValidation:
    def test_rejects_bad_missing_policy(self):
        with pytest.raises(ConfigError):
            load_config_from({"missing": {"policy": "nonsense"}})

    def test_rejects_unknown_provider(self):
        with pytest.raises(ConfigError):
            load_config_from({"verification": {"providers": ["scihub"]}})

    def test_rejects_inverted_thresholds(self):
        with pytest.raises(ConfigError):
            load_config_from(
                {"verification": {"accept_threshold": 0.5, "review_threshold": 0.9}}
            )

    def test_accepts_partial_config(self):
        config = load_config_from({"missing": {"policy": "omit"}})
        assert config["missing"]["policy"] == "omit"
        # Untouched keys keep their defaults.
        assert config["missing"]["placeholder"] == "MISSING"


def load_config_from(data):
    """Write a config dict to a temp file and load it, exercising the real path."""
    import json
    import os
    import tempfile

    fd, path = tempfile.mkstemp(suffix=".json")
    with os.fdopen(fd, "w") as handle:
        json.dump(data, handle)
    try:
        return load_config(path)
    finally:
        os.remove(path)


class TestTrustedAbsence:
    """A verified record that carries no issue number means the journal has
    none — not that we failed to find it. Writing MISSING there puts a literal
    `no.~MISSING` in the typeset bibliography."""

    def test_verified_journal_without_issue_omits_number(self, config):
        record = Record(source="crossref", title="T", year="2021", kind=JOURNAL,
                        venue="IEEE/ACM Transactions on Audio, Speech, and Language Processing",
                        authors=["Ada Lovelace"], volume="29", pages="3451--3460",
                        doi="10.1109/TASLP.2021.3122291")
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "Ada Lovelace",
             "title": "T", "year": "2021", "doi": "10.1109/TASLP.2021.3122291"},
            config, verified(record),
        )
        assert "number" not in result.fields
        assert "number" not in result.missing
        # A legitimately absent field is not a schema violation.
        assert validate(result, config) == []

    def test_note_is_never_treated_as_legitimately_absent(self, config):
        # Rule 6 requires a link on every entry, so a missing one is a real gap
        # even when the record is verified.
        record = Record(source="crossref", title="T", year="2021", kind=JOURNAL,
                        venue="Neural Computation", authors=["Ada Lovelace"],
                        volume="29")
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "Ada Lovelace",
             "title": "T", "year": "2021"},
            config, verified(record),
        )
        assert result.fields["note"] == "MISSING"
        assert "note" in result.missing

    def test_verified_conference_without_pages_omits_pages(self, config):
        record = Record(source="dblp", title="T", year="2021", kind=CONFERENCE,
                        venue="ICLR", authors=["Ada Lovelace"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Ada Lovelace",
             "title": "T", "year": "2021"},
            config, verified(record),
        )
        assert "pages" not in result.fields

    def test_unverified_entry_still_gets_a_placeholder(self, config):
        # Here the data really is unknown, so the gap must stay visible.
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "A B", "title": "T",
             "year": "2020"},
            config,
        )
        assert result.fields["number"] == "MISSING"
        assert "number" in result.missing

    def test_known_value_is_still_used(self, config):
        record = Record(source="crossref", title="T", year="2014", kind=JOURNAL,
                        venue="Neural Computation", authors=["Ada Lovelace"],
                        volume="22", number="4", pages="745--777")
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "Ada Lovelace",
             "title": "T", "year": "2014"},
            config, verified(record),
        )
        assert result.fields["number"] == "4"

    def test_can_be_disabled_for_strict_field_counts(self, config):
        config["missing"]["trust_verified_absence"] = []
        record = Record(source="crossref", title="T", year="2021", kind=JOURNAL,
                        venue="Neural Computation", authors=["Ada Lovelace"],
                        volume="29", pages="3451--3460")
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "Ada Lovelace",
             "title": "T", "year": "2021"},
            config, verified(record),
        )
        assert result.fields["number"] == "MISSING"


class TestLinkFallbacks:
    def test_arxiv_id_becomes_the_link_when_there_is_no_doi(self, config):
        # ICLR, JMLR and older workshops register no DOI; an arXiv id we found
        # along the way beats emitting MISSING.
        record = Record(source="dblp", title="T", year="2017", kind=CONFERENCE,
                        venue="ICLR", authors=["Noam Shazeer"],
                        arxiv_id="1701.06538")
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Noam Shazeer",
             "title": "T", "year": "2017"},
            config, verified(record),
        )
        assert result.fields["note"] == r"\url{https://arxiv.org/abs/1701.06538}"

    def test_local_arxiv_id_is_used_when_the_record_has_none(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "A B", "title": "T",
             "year": "2017", "eprint": "1701.06538", "archiveprefix": "arXiv"},
            config,
        )
        assert result.fields["note"] == r"\url{https://arxiv.org/abs/1701.06538}"

    def test_doi_still_wins_over_arxiv(self, config):
        record = Record(source="crossref", title="T", year="2017", kind=CONFERENCE,
                        venue="ICML", authors=["A B"], doi="10.1234/real",
                        arxiv_id="1701.06538")
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "A B",
             "title": "T", "year": "2017"},
            config, verified(record),
        )
        assert result.fields["note"] == r"\url{https://doi.org/10.1234/real}"


class TestPreprintFieldLeakage:
    def test_volume_is_not_taken_from_a_preprint_record(self, config):
        # DBLP files preprints under a pseudo-volume "abs/2101.03961", which
        # would otherwise be typeset as "vol. abs/2101.03961".
        record = Record(source="dblp", title="T", year="2021", kind=PREPRINT,
                        venue="CoRR", authors=["William Fedus"],
                        volume="abs/2101.03961")
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "William Fedus",
             "title": "T", "year": "2021", "journal": "JMLR"},
            config, verified(record),
        )
        assert result.fields.get("volume") != "abs/2101.03961"

    def test_volume_is_still_taken_from_a_published_record(self, config):
        record = Record(source="dblp", title="T", year="2022", kind=JOURNAL,
                        venue="Journal of Machine Learning Research",
                        authors=["William Fedus"], volume="23", number="120")
        result = build(
            {"ID": "x", "ENTRYTYPE": "article", "author": "William Fedus",
             "title": "T", "year": "2022"},
            config, verified(record),
        )
        assert result.fields["volume"] == "23"
        assert result.fields["number"] == "120"


class TestDblpConventions:
    """DBLP encodes two things in ways that must not reach a .bib verbatim."""

    def test_article_pagination_is_split_into_number_and_pages(self):
        from bibformatter.providers import DblpProvider
        record = DblpProvider.__new__(DblpProvider)._to_record({
            "title": "Switch Transformers", "volume": "23",
            "pages": "120:1-120:39", "type": "Journal Articles",
            "year": "2022", "venue": "J. Mach. Learn. Res.",
        })
        # JMLR paginates per article: article 120, pages 1-39.
        assert record.volume == "23"
        assert record.number == "120"
        assert record.pages == "1--39"

    def test_ordinary_pagination_is_untouched(self):
        from bibformatter.providers import DblpProvider
        record = DblpProvider.__new__(DblpProvider)._to_record({
            "title": "X", "volume": "22", "number": "4", "pages": "745-777",
            "type": "Journal Articles", "year": "2014",
        })
        assert (record.volume, record.number, record.pages) == ("22", "4", "745--777")

    def test_pseudo_volume_is_rejected_and_kept_as_an_arxiv_id(self):
        from bibformatter.providers import _fix_preprint_kind, Record as R
        record = _fix_preprint_kind(
            R(source="s2", title="T", kind=JOURNAL, venue="JMLR",
              volume="abs/2101.03961")
        )
        assert record.volume == ""
        assert record.arxiv_id == "2101.03961"

    def test_real_volume_survives_sanitizing(self):
        from bibformatter.providers import _fix_preprint_kind, Record as R
        record = _fix_preprint_kind(
            R(source="x", title="T", kind=JOURNAL, venue="Neural Computation",
              volume="23")
        )
        assert record.volume == "23"


class TestFuzzyRecordIsNotTrusted:
    """A fuzzy candidate was rejected as probably-a-different-paper. It must not
    contribute *any* data, identifiers included."""

    WRONG = Record(source="crossref", title="A Different Paper", year="2023",
                   kind=CONFERENCE, venue="EMNLP", doi="10.1234/wrong",
                   url="https://example.org/wrong", authors=["Someone Else"],
                   pages="11329--11344")

    def fuzzy(self):
        from bibformatter.verify import FUZZY
        return Verification(status=FUZZY, record=self.WRONG, source="crossref")

    def test_fuzzy_doi_does_not_become_the_note(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Ada Lovelace",
             "title": "The Real Paper", "year": "2021"},
            config, self.fuzzy(),
        )
        assert "10.1234/wrong" not in result.fields["note"]
        assert "example.org/wrong" not in result.fields["note"]

    def test_fuzzy_record_does_not_supply_pages(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Ada Lovelace",
             "title": "The Real Paper", "year": "2021"},
            config, self.fuzzy(),
        )
        assert result.fields.get("pages") != "11329--11344"

    def test_local_identifiers_are_still_used(self, config):
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "author": "Ada Lovelace",
             "title": "The Real Paper", "year": "2021", "doi": "10.5555/mine"},
            config, self.fuzzy(),
        )
        assert result.fields["note"] == r"\url{https://doi.org/10.5555/mine}"


class TestTitleCompleteness:
    """Databases frequently store a title without its subtitle. Replacing a
    fuller local title with a truncated one loses information."""

    def test_truncated_remote_title_does_not_replace_a_fuller_local_one(self, config):
        record = Record(source="dblp", title="Automatic Speech Recognition",
                        year="2015", kind=BOOK, authors=["Dong Yu", "Li Deng"],
                        publisher="Springer")
        result = build(
            {"ID": "x", "ENTRYTYPE": "book",
             "title": "Automatic speech recognition: A deep learning approach",
             "author": "Dong Yu and Li Deng", "year": "2015",
             "publisher": "Springer"},
            config, verified(record),
        )
        assert "deep learning approach" in result.fields["title"].lower()

    def test_fuller_remote_title_still_wins(self, config):
        # The real AMI paper is titled "...: A Pre-announcement".
        record = Record(source="crossref",
                        title="The AMI Meeting Corpus: A Pre-announcement",
                        year="2005", kind=CONFERENCE, venue="MLMI",
                        authors=["Jean Carletta"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "title": "The AMI Meeting Corpus",
             "author": "Jean Carletta", "year": "2005"},
            config, verified(record),
        )
        assert result.fields["title"].endswith("Pre-announcement")

    def test_a_genuinely_different_title_still_replaces(self, config):
        record = Record(source="crossref", title="The Corrected Title",
                        year="2005", kind=CONFERENCE, venue="ICML",
                        authors=["Ada Lovelace"])
        result = build(
            {"ID": "x", "ENTRYTYPE": "inproceedings", "title": "The Wrong Titel",
             "author": "Ada Lovelace", "year": "2005"},
            config, verified(record),
        )
        assert "Corrected" in result.fields["title"]
