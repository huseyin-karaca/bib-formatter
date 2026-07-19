"""Verification orchestration, with stub providers instead of the network."""

import pytest

from bibformatter.config import load_config
from bibformatter.providers import BOOK, CONFERENCE, JOURNAL, MISC, PREPRINT, Record
from bibformatter.verify import FUZZY, SKIPPED, UNVERIFIED, VERIFIED, Verifier


class StubProvider:
    """Answers with a fixed list of records, and counts how often it was asked."""

    def __init__(self, name, records=(), by_doi=None):
        self.name = name
        self._records = list(records)
        self._by_doi = by_doi
        self.searches = 0
        self.doi_lookups = 0

    def search(self, title, authors, year):
        self.searches += 1
        return list(self._records)

    def by_doi(self, doi):
        self.doi_lookups += 1
        return self._by_doi

    def by_id(self, arxiv_id):
        return self._records[0] if self._records else None


class DeadProvider:
    """A provider that raises, to prove one bad database can't sink a run."""

    name = "dead"

    def search(self, title, authors, year):
        raise RuntimeError("provider exploded")

    def by_doi(self, doi):
        raise RuntimeError("provider exploded")


@pytest.fixture
def config():
    config = load_config()
    config["network"]["enabled"] = True
    config["network"]["cache_path"] = None
    return config


def make_verifier(config, providers, order=None):
    verifier = Verifier(config, http=_NoHttp())
    verifier.providers = providers
    verifier.settings["providers"] = order or list(providers)
    verifier.enabled = True
    return verifier


class _NoHttp:
    """Fails loudly if anything tries to make a real request."""

    mailto = None
    stats = {}

    def get_json(self, *a, **k):
        raise AssertionError("test attempted a network call")

    get_text = get_json


PAPER = Record(
    source="dblp", title="Attention Is All You Need",
    authors=["Ashish Vaswani", "Noam Shazeer"], year="2017",
    kind=CONFERENCE, venue="NeurIPS", pages="5998--6008",
)


class TestFabricationDetection:
    def test_unknown_reference_is_unverified(self, config):
        verifier = make_verifier(config, {"crossref": StubProvider("crossref", [])})
        result = verifier.verify(
            "MOE-STP: Mixture-of-Experts for Scalable Speech Translation",
            ["Some Author"], "2020", None, None,
        )
        assert result.status == UNVERIFIED
        assert "no database returned a matching record" in result.notes

    def test_wrong_but_plausible_match_is_fuzzy_not_verified(self, config):
        # A real near-miss pair: similar wording, different paper, different authors.
        near_miss = Record(
            source="crossref",
            title="Hierarchical Ensemble-Based Feature Selection for Time Series Prediction",
            authors=["Someone Else"], year="2019", kind=JOURNAL,
        )
        verifier = make_verifier(config, {"crossref": StubProvider("crossref", [near_miss])})
        result = verifier.verify(
            "Ensemble-based hierarchical feature selection for prediction of time series data",
            ["Suleyman Kozat"], "2012", None, None,
        )
        assert result.status == FUZZY
        # The candidate is retained so the report can show what it nearly matched.
        assert result.record is not None

    def test_every_provider_is_tried_before_giving_up(self, config):
        providers = {
            "crossref": StubProvider("crossref", []),
            "dblp": StubProvider("dblp", []),
            "openalex": StubProvider("openalex", []),
        }
        verifier = make_verifier(config, providers)
        result = verifier.verify("A Nonexistent Paper", ["Nobody"], "2020", None, None)
        assert result.status == UNVERIFIED
        assert all(p.searches == 1 for p in providers.values())

    def test_all_attempts_are_recorded_for_the_report(self, config):
        providers = {
            "crossref": StubProvider("crossref", []),
            "dblp": StubProvider("dblp", []),
        }
        verifier = make_verifier(config, providers)
        result = verifier.verify("A Nonexistent Paper", ["Nobody"], "2020", None, None)
        assert [a.provider for a in result.attempts] == ["crossref", "dblp"]


class TestProviderFallback:
    def test_falls_through_to_the_next_database(self, config):
        providers = {
            "crossref": StubProvider("crossref", []),
            "dblp": StubProvider("dblp", [PAPER]),
        }
        verifier = make_verifier(config, providers)
        result = verifier.verify(
            "Attention Is All You Need", ["Ashish Vaswani"], "2017", None, None
        )
        assert result.status == VERIFIED
        assert result.source == "dblp"

    def test_stops_at_the_first_confident_match(self, config):
        providers = {
            "crossref": StubProvider("crossref", [PAPER]),
            "dblp": StubProvider("dblp", [PAPER]),
        }
        verifier = make_verifier(config, providers)
        verifier.verify("Attention Is All You Need", ["Ashish Vaswani"], "2017", None, None)
        assert providers["dblp"].searches == 0

    def test_a_raising_provider_does_not_abort_the_run(self, config):
        providers = {"dead": DeadProvider(), "dblp": StubProvider("dblp", [PAPER])}
        verifier = make_verifier(config, providers, order=["dead", "dblp"])
        result = verifier.verify(
            "Attention Is All You Need", ["Ashish Vaswani"], "2017", None, None
        )
        assert result.status == VERIFIED
        assert result.source == "dblp"


class TestDoiHandling:
    def test_matching_doi_verifies_immediately(self, config):
        record = Record(source="crossref", title="Attention Is All You Need",
                        authors=["Ashish Vaswani"], year="2017", kind=CONFERENCE,
                        doi="10.1234/abc")
        providers = {"crossref": StubProvider("crossref", [], by_doi=record)}
        verifier = make_verifier(config, providers)
        result = verifier.verify("Attention Is All You Need", ["Ashish Vaswani"],
                                 "2017", "10.1234/abc", None)
        assert result.status == VERIFIED
        assert "DOI" in result.source

    def test_doi_pointing_at_the_wrong_paper_is_reported(self, config):
        wrong = Record(source="crossref", title="A Totally Different Paper",
                       authors=["Someone Else"], year="1999", kind=JOURNAL,
                       doi="10.1234/wrong")
        providers = {
            "crossref": StubProvider("crossref", [], by_doi=wrong),
            "dblp": StubProvider("dblp", [PAPER]),
        }
        verifier = make_verifier(config, providers)
        result = verifier.verify("Attention Is All You Need", ["Ashish Vaswani"],
                                 "2017", "10.1234/wrong", None)
        # The mismatch is surfaced, and the correct record is found elsewhere.
        assert any("resolves to a different work" in n for n in result.notes)
        assert result.status == VERIFIED
        assert result.source == "dblp"


class TestPreprintPromotion:
    def test_arxiv_doi_does_not_short_circuit_the_search(self, config):
        # 10.48550/... resolves to the preprint. The published version should
        # still win.
        arxiv_doi_record = Record(
            source="crossref", title="Attention Is All You Need",
            authors=["Ashish Vaswani"], year="2017", kind=PREPRINT,
            venue="arXiv", doi="10.48550/arXiv.1706.03762",
        )
        providers = {
            "crossref": StubProvider("crossref", [], by_doi=arxiv_doi_record),
            "dblp": StubProvider("dblp", [PAPER]),
        }
        verifier = make_verifier(config, providers)
        result = verifier.verify(
            "Attention Is All You Need", ["Ashish Vaswani"], "2017",
            "10.48550/arXiv.1706.03762", None,
        )
        assert result.record.kind == CONFERENCE
        assert result.record.venue == "NeurIPS"

    def test_arxiv_doi_is_kept_when_nothing_else_has_it(self, config):
        arxiv_doi_record = Record(
            source="crossref", title="Deep Speech", authors=["Awni Hannun"],
            year="2014", kind=PREPRINT, venue="arXiv", doi="10.48550/arXiv.1412.5567",
        )
        providers = {
            "crossref": StubProvider("crossref", [], by_doi=arxiv_doi_record),
            "dblp": StubProvider("dblp", []),
        }
        verifier = make_verifier(config, providers)
        result = verifier.verify("Deep Speech", ["Awni Hannun"], "2014",
                                 "10.48550/arXiv.1412.5567", None)
        assert result.status == VERIFIED
        assert result.record.kind == PREPRINT

    def test_published_version_wins_over_preprint(self, config):
        preprint = Record(source="crossref", title="Attention Is All You Need",
                          authors=["Ashish Vaswani"], year="2017", kind=PREPRINT,
                          venue="arXiv")
        providers = {
            "crossref": StubProvider("crossref", [preprint]),
            "dblp": StubProvider("dblp", [PAPER]),
        }
        verifier = make_verifier(config, providers)
        result = verifier.verify("Attention Is All You Need", ["Ashish Vaswani"],
                                 "2017", None, None)
        assert result.record.kind == CONFERENCE
        assert result.record.venue == "NeurIPS"

    def test_genuine_preprint_stays_a_preprint(self, config):
        preprint = Record(source="crossref", title="Deep Speech", kind=PREPRINT,
                          authors=["Awni Hannun"], year="2014", venue="arXiv")
        providers = {
            "crossref": StubProvider("crossref", [preprint]),
            "dblp": StubProvider("dblp", []),
        }
        verifier = make_verifier(config, providers)
        result = verifier.verify("Deep Speech", ["Awni Hannun"], "2014", None, None)
        assert result.status == VERIFIED
        assert result.record.kind == PREPRINT

    def test_inconclusive_match_does_not_stop_the_search(self, config):
        vague = Record(source="crossref", title="Attention Is All You Need",
                       authors=["Ashish Vaswani"], year="2017", kind=MISC)
        providers = {
            "crossref": StubProvider("crossref", [vague]),
            "dblp": StubProvider("dblp", [PAPER]),
        }
        verifier = make_verifier(config, providers)
        verifier.verify("Attention Is All You Need", ["Ashish Vaswani"], "2017", None, None)
        assert providers["dblp"].searches == 1


class TestDisabled:
    def test_verification_can_be_switched_off(self, config):
        config["verification"]["enabled"] = False
        verifier = Verifier(config, http=_NoHttp())
        result = verifier.verify("Anything", [], "2020", None, None)
        assert result.status == SKIPPED

    def test_cached_only_still_verifies(self, config):
        # Switching off requests must not switch off verification: the HTTP
        # client keeps serving cache hits, which is what --cached-only replays.
        config["network"]["enabled"] = False
        verifier = make_verifier(config, {"dblp": StubProvider("dblp", [PAPER])})
        result = verifier.verify(
            "Attention Is All You Need", ["Ashish Vaswani"], "2017", None, None
        )
        assert result.status == VERIFIED
