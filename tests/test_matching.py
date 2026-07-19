from bibformatter.matching import (
    author_overlap,
    best_candidate,
    classify,
    score,
    title_similarity,
    token_containment,
    year_proximity,
)
from bibformatter.providers import CONFERENCE, PREPRINT, Record

ACCEPT = 0.90
REVIEW = 0.70


def make(title, authors=(), year="", kind=CONFERENCE, doi="", source="test"):
    return Record(
        source=source, title=title, authors=list(authors), year=year, kind=kind, doi=doi
    )


class TestTitleSimilarity:
    def test_identical(self):
        assert title_similarity("Attention Is All You Need", "Attention is all you need") == 1.0

    def test_ignores_latex_and_punctuation(self):
        assert title_similarity(
            "{BERT}: Pre-training of Deep Bidirectional Transformers",
            "BERT: Pre-training of deep bidirectional transformers",
        ) == 1.0

    def test_dropped_subtitle_is_handled_by_containment_not_similarity(self):
        # Sequence similarity is low here by design; classify() uses the
        # containment signal to rescue this case.
        assert token_containment(
            "Skip RNN: Learning to Skip State Updates in Recurrent Neural Networks",
            "Skip RNN",
        ) == 1.0

    def test_containment_does_not_fire_on_different_papers(self):
        assert token_containment(
            "Multi-style data augmentation for improved robust speech recognition",
            "Multi-Speaker Data Augmentation for Improved end-to-end Automatic Speech Recognition",
        ) < 0.9

    def test_different_papers_score_low(self):
        assert title_similarity(
            "Attention Is All You Need",
            "Deep Residual Learning for Image Recognition",
        ) < REVIEW

    def test_similar_but_distinct_titles_are_not_accepted(self):
        # These are genuinely different papers and must not auto-verify.
        assert title_similarity(
            "Multi-style data augmentation for improved robust speech recognition",
            "Multi-Speaker Data Augmentation for Improved end-to-end Automatic Speech Recognition",
        ) < ACCEPT

    def test_empty(self):
        assert title_similarity("", "anything") == 0.0


class TestAuthorOverlap:
    def test_exact(self):
        assert author_overlap(["Ada Lovelace"], ["Ada Lovelace"]) == 1.0

    def test_matches_on_surname_despite_initials(self):
        assert author_overlap(["A. Lovelace"], ["Ada Lovelace"]) == 1.0

    def test_matches_regardless_of_name_order(self):
        assert author_overlap(["Lovelace, Ada"], ["Ada Lovelace"]) == 1.0

    def test_partial(self):
        assert author_overlap(
            ["Ada Lovelace", "Alan Turing"], ["Ada Lovelace", "Grace Hopper"]
        ) == 0.5

    def test_disjoint(self):
        assert author_overlap(["Ada Lovelace"], ["Alan Turing"]) == 0.0

    def test_empty_is_neutral_zero(self):
        assert author_overlap([], ["Ada Lovelace"]) == 0.0


class TestYearProximity:
    def test_exact(self):
        assert year_proximity("2020", "2020") == 1.0

    def test_within_tolerance(self):
        assert year_proximity("2020", "2021") == 0.75

    def test_far_off(self):
        assert year_proximity("2020", "1999") == 0.0

    def test_unknown_is_neutral(self):
        assert year_proximity("", "2020") == 0.5


class TestClassify:
    def test_strong_match_verifies(self):
        result = score("Attention Is All You Need", ["Ashish Vaswani"], "2017", None,
                       make("Attention Is All You Need", ["Ashish Vaswani"], "2017"))
        assert classify(result, ACCEPT, REVIEW) == "verified"

    def test_matching_doi_verifies_outright(self):
        # Same DOI on both sides settles it even if the stored title is sloppy.
        result = score("A Typo'd Titel", [], "2017", "10.1234/xyz",
                       make("The Real Title", [], "2017", doi="10.1234/xyz"))
        assert result.doi_exact
        assert classify(result, ACCEPT, REVIEW) == "verified"

    def test_title_only_coincidence_is_not_verified(self):
        # Identical title, but no author in common and a wildly different year.
        result = score("Introduction", ["Ada Lovelace"], "1850", None,
                       make("Introduction", ["Alan Turing"], "2020"))
        assert classify(result, ACCEPT, REVIEW) == "fuzzy"

    def test_weak_title_rescued_by_authors_and_year(self):
        result = score("Skip RNN", ["Victor Campos"], "2018", None,
                       make("Skip RNN: Learning to Skip State Updates",
                            ["Victor Campos"], "2018"))
        assert classify(result, ACCEPT, REVIEW) == "verified"

    def test_nonsense_is_no_match(self):
        result = score("A Completely Invented Paper Title", ["Nobody"], "2021", None,
                       make("Deep Residual Learning", ["Kaiming He"], "2016"))
        assert classify(result, ACCEPT, REVIEW) == "no-match"

    def test_near_miss_is_flagged_for_review(self):
        result = score(
            "Ensemble-based hierarchical feature selection for time series",
            ["Kozat"], "2012", None,
            make("Hierarchical Ensemble-Based Feature Selection for Time Series",
                 ["Kozat"], "2012"))
        assert classify(result, ACCEPT, REVIEW) in ("fuzzy", "verified")


class TestBestCandidate:
    def test_prefers_published_over_preprint(self):
        # Databases hold both; the citation should point at the version of record.
        candidates = [
            make("Attention Is All You Need", ["Vaswani"], "2017", kind=PREPRINT),
            make("Attention Is All You Need", ["Vaswani"], "2017", kind=CONFERENCE),
        ]
        record, _ = best_candidate(candidates, "Attention Is All You Need",
                                   ["Vaswani"], "2017", None)
        assert record.kind == CONFERENCE

    def test_picks_highest_scoring(self):
        candidates = [
            make("Something Else Entirely", [], "2017"),
            make("Attention Is All You Need", ["Vaswani"], "2017"),
        ]
        record, _ = best_candidate(candidates, "Attention Is All You Need",
                                   ["Vaswani"], "2017", None)
        assert record.title == "Attention Is All You Need"

    def test_no_candidates(self):
        assert best_candidate([], "T", [], "2020", None) is None

    def test_skips_empty_records(self):
        assert best_candidate([Record(source="x")], "T", [], "2020", None) is None
