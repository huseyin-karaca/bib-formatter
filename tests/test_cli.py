"""End-to-end tests.

These run with --offline so they are fast, deterministic and never touch the
network. Verification logic itself is covered in test_matching / test_verify.
"""

import json
import os

import bibtexparser
import pytest

from bibformatter.cli import main
from bibformatter.parser import find_duplicate_keys, load_entries

FIXTURE = os.path.join(os.path.dirname(__file__), "fixtures", "sample.bib")


@pytest.fixture
def formatted(tmp_path):
    """Run the CLI offline over the fixture and return the parsed output."""
    out = tmp_path / "out.bib"
    report = tmp_path / "report.json"
    code = main([
        FIXTURE, "-o", str(out), "--offline", "--no-color",
        "--report", str(report),
    ])
    assert code == 0
    text = out.read_text(encoding="utf-8")
    entries = {e["ID"]: e for e in bibtexparser.loads(text).entries}
    return text, entries, json.loads(report.read_text(encoding="utf-8"))


class TestEndToEnd:
    def test_output_is_valid_bibtex(self, formatted):
        text, entries, _ = formatted
        # Every entry from the input survives (minus the duplicate key collapse).
        assert len(entries) >= 7

    def test_control_entry_is_copied_verbatim(self, formatted):
        text, _, _ = formatted
        # Original casing preserved, not lowercased by the parser.
        assert "@IEEEtranBSTCTL{IEEEexample:BSTcontrol," in text
        assert "CTLmax_names_forced_etal" in text

    def test_article_schema_is_exact(self, formatted):
        _, entries, _ = formatted
        fields = set(entries["li2014overview"]) - {"ID", "ENTRYTYPE"}
        assert fields == {
            "author", "journal", "note", "number", "pages", "title", "volume", "year"
        }

    def test_noise_fields_are_dropped(self, formatted):
        _, entries, _ = formatted
        assert "abstract" not in entries["li2014overview"]
        assert "keywords" not in entries["li2014overview"]

    def test_pages_are_normalized(self, formatted):
        _, entries, _ = formatted
        assert entries["li2014overview"]["pages"] == "745--777"

    def test_doi_goes_into_note_as_url(self, formatted):
        _, entries, _ = formatted
        note = entries["li2014overview"]["note"]
        assert note == r"\url{https://doi.org/10.1109/TASLP.2014.2304637}"

    def test_doi_case_is_preserved(self, formatted):
        _, entries, _ = formatted
        assert "TASLP" in entries["li2014overview"]["note"]

    def test_authors_are_first_last_separated_by_and(self, formatted):
        _, entries, _ = formatted
        author = entries["li2014overview"]["author"]
        # The separator carries a newline, since each author goes on its own line.
        assert author.split(" and\n") == ["Jinyu Li", "Li Deng", "Yifan Gong"]

    def test_authors_are_one_per_line(self, formatted):
        text, _, _ = formatted
        assert "Jinyu Li and\n" in text

    def test_venue_acronym_is_expanded(self, formatted):
        _, entries, _ = formatted
        assert entries["someone2020conf"]["booktitle"] == (
            "International Conference on Machine Learning (ICML)"
        )

    def test_book_omits_absent_edition(self, formatted):
        _, entries, _ = formatted
        assert "edition" not in entries["lattimore2020bandit"]

    def test_book_without_doi_gets_missing_note(self, formatted):
        _, entries, _ = formatted
        assert entries["lattimore2020bandit"]["note"] == "MISSING"

    def test_web_resource_keeps_its_url_and_prose(self, formatted):
        _, entries, _ = formatted
        note = entries["torchdocs"]["note"]
        assert r"\url{" in note
        assert "Accessed" in note

    def test_escaped_ampersand_is_not_double_escaped(self, formatted):
        _, entries, _ = formatted
        journal = entries["amp2019journal"]["journal"]
        assert "textbackslash" not in journal

    def test_entries_are_sorted_by_key(self, formatted):
        text, _, _ = formatted
        keys = [
            line.split("{")[1].rstrip(",")
            for line in text.splitlines()
            if line.startswith("@") and "{" in line
        ]
        # The control entry stays first; the rest are alphabetical.
        assert keys[1:] == sorted(keys[1:], key=str.lower)

    def test_report_lists_duplicate_keys(self, formatted):
        _, _, report = formatted
        assert "dupe2021" in report["duplicate_keys"]

    def test_report_lists_missing_fields(self, formatted):
        _, _, report = formatted
        keys = {item["key"] for item in report["missing_entries"]}
        assert "lattimore2020bandit" in keys


class TestCliBehaviour:
    def test_missing_omit_policy(self, tmp_path):
        out = tmp_path / "out.bib"
        assert main([FIXTURE, "-o", str(out), "--offline", "--missing", "omit",
                     "--no-color", "-q"]) == 0
        assert "MISSING" not in out.read_text(encoding="utf-8")

    def test_check_writes_no_output(self, tmp_path):
        out = tmp_path / "out.bib"
        assert main([FIXTURE, "-o", str(out), "--offline", "--check",
                     "--no-color"]) == 0
        assert not out.exists()

    def test_strict_fails_on_problems(self, tmp_path):
        # The fixture has unresolved MISSING fields and a duplicate key.
        assert main([FIXTURE, "--offline", "--check", "--strict",
                     "--no-color"]) == 1

    def test_cached_only_makes_no_requests(self, tmp_path):
        # Verification runs, but with an empty cache nothing can be confirmed —
        # and crucially, no request is attempted (this test has no network).
        config = tmp_path / "c.json"
        config.write_text(json.dumps(
            {"network": {"cache_path": str(tmp_path / "cache.json")}}
        ))
        report = tmp_path / "r.json"
        code = main([FIXTURE, "--check", "--cached-only", "-c", str(config),
                     "--report", str(report), "--no-color"])
        assert code == 0
        summary = json.loads(report.read_text())
        assert summary["verified"] == 0
        assert summary["unverified"] > 0

    def test_bad_config_is_rejected(self, tmp_path):
        bad = tmp_path / "bad.json"
        bad.write_text(json.dumps({"missing": {"policy": "nope"}}))
        assert main([FIXTURE, "--offline", "-c", str(bad), "--no-color"]) == 2

    def test_missing_input_file(self):
        assert main(["definitely-not-a-file.bib", "--offline", "--no-color"]) == 2

    def test_config_file_is_applied(self, tmp_path):
        config = tmp_path / "c.json"
        config.write_text(json.dumps({"authors": {"name_order": "last-first"}}))
        out = tmp_path / "out.bib"
        assert main([FIXTURE, "-o", str(out), "--offline", "-c", str(config),
                     "--no-color", "-q"]) == 0
        entries = {e["ID"]: e for e in bibtexparser.loads(
            out.read_text(encoding="utf-8")).entries}
        assert entries["li2014overview"]["author"].startswith("Li, Jinyu")

    def test_output_is_stable_across_runs(self, tmp_path):
        first, second = tmp_path / "a.bib", tmp_path / "b.bib"
        main([FIXTURE, "-o", str(first), "--offline", "--no-color", "-q"])
        main([FIXTURE, "-o", str(second), "--offline", "--no-color", "-q"])
        assert first.read_text() == second.read_text()

    def test_formatting_is_idempotent(self, tmp_path):
        once, twice = tmp_path / "1.bib", tmp_path / "2.bib"
        main([FIXTURE, "-o", str(once), "--offline", "--no-color", "-q"])
        main([str(once), "-o", str(twice), "--offline", "--no-color", "-q"])
        # Re-formatting already-formatted output must not change it further.
        body_once = [l for l in once.read_text().splitlines() if not l.startswith("%")]
        body_twice = [l for l in twice.read_text().splitlines() if not l.startswith("%")]
        assert body_once == body_twice


class TestParser:
    def test_finds_duplicate_keys(self):
        entries, _ = load_entries(FIXTURE)
        assert find_duplicate_keys(entries) == {"dupe2021": 2}

    def test_captures_raw_sources(self):
        _, raws = load_entries(FIXTURE)
        assert raws["lattimore2020bandit"].startswith("@book{lattimore2020bandit,")
        assert raws["lattimore2020bandit"].rstrip().endswith("}")


class TestDeduplication:
    """A repeated citation key makes BibTeX fail outright, so the output must
    never contain one — whatever the input held."""

    def test_output_has_no_repeated_keys(self, formatted):
        text, _, _ = formatted
        keys = [
            line.split("{", 1)[1].rstrip(",")
            for line in text.splitlines()
            if line.startswith("@") and "{" in line
        ]
        assert len(keys) == len(set(keys)), "duplicate key would break BibTeX"

    def test_duplicate_is_reported_as_merged(self, formatted):
        _, _, report = formatted
        dropped = {d["key"]: d for d in report["dropped_duplicates"]}
        assert "dupe2021" in dropped

    def test_conflicting_duplicates_are_flagged_as_not_identical(self, formatted):
        # The fixture's two dupe2021 entries are different works.
        _, _, report = formatted
        dropped = {d["key"]: d for d in report["dropped_duplicates"]}
        assert dropped["dupe2021"]["identical"] is False

    def test_first_definition_wins(self, formatted):
        # BibTeX itself keeps the first, so the output must agree with it.
        _, entries, _ = formatted
        assert entries["dupe2021"]["title"] == "First Version"
