from bibformatter.normalize import (
    extract_arxiv_id,
    extract_doi,
    format_name,
    is_corporate,
    latex_to_text,
    looks_like_initials,
    normalize_pages,
    normalize_year,
    parse_name,
    split_authors,
    text_to_latex,
)


class TestLatex:
    def test_decodes_accents(self):
        assert latex_to_text(r"Nicol{\`o} Cesa-Bianchi") == "Nicolò Cesa-Bianchi"
        assert latex_to_text(r"Andr\'{e} Susano Pinto") == "André Susano Pinto"

    def test_strips_protective_braces(self):
        assert latex_to_text("{BERT}: Pre-training") == "BERT: Pre-training"

    def test_survives_broken_markup(self):
        # Must not raise on the kind of mangled input real .bib files contain.
        assert latex_to_text(r"{{\{}GS{\}}hard") != ""

    def test_encodes_back_to_latex(self):
        assert text_to_latex("Nicolò") == r"Nicol\`o"
        assert text_to_latex("Łukasz") == r"{\L}ukasz"

    def test_round_trip_is_stable(self):
        for name in ["Víctor Campos", "Xavier Giró-i-Nieto", "Łukasz Kaiser"]:
            assert latex_to_text(text_to_latex(name)) == name

    def test_protects_acronyms(self):
        assert text_to_latex("BERT for ASR", protect_caps=True) == "{BERT} for {ASR}"

    def test_protects_camel_case(self):
        # BibTeX would otherwise render these as "Pytorch" / "Gshard".
        assert text_to_latex("PyTorch", protect_caps=True) == "{PyTorch}"
        assert text_to_latex("GShard", protect_caps=True) == "{GShard}"

    def test_leaves_ordinary_title_case_alone(self):
        for title in [
            "Deep Learning",
            "A Framework for Self-Supervised Learning",
            "Convergence via Over-Parameterization",
        ]:
            assert "{" not in text_to_latex(title, protect_caps=True)

    def test_protects_acronyms_with_digits_and_hyphens(self):
        assert text_to_latex("The CHiME-7 DASR Challenge", protect_caps=True) == (
            "The {CHiME-7} {DASR} Challenge"
        )

    def test_escapes_specials(self):
        assert text_to_latex("Speech & Language") == r"Speech \& Language"
        assert text_to_latex("100% coverage") == r"100\% coverage"
        assert text_to_latex("lr_scheduler") == r"lr\_scheduler"

    def test_specials_survive_a_decode_encode_round_trip(self):
        # The contract is plain text in; callers decode first. This is the path
        # every .bib value actually takes.
        for value in [r"Speech \& Language", r"100\% coverage"]:
            assert text_to_latex(latex_to_text(value)) == value


class TestAuthors:
    def test_splits_on_and(self):
        assert split_authors("Ada Lovelace and Alan Turing") == [
            "Ada Lovelace",
            "Alan Turing",
        ]

    def test_does_not_split_inside_braces(self):
        assert split_authors("{Ford and Sons} and Ada Lovelace") == [
            "{Ford and Sons}",
            "Ada Lovelace",
        ]

    def test_handles_newlines(self):
        assert len(split_authors("A One and\n  B Two and\n  C Three")) == 3

    def test_empty(self):
        assert split_authors("") == []
        assert split_authors(None) == []

    def test_parse_last_first(self):
        assert parse_name("Lovelace, Ada") == ("Ada", "Lovelace")

    def test_parse_first_last(self):
        assert parse_name("Ada Lovelace") == ("Ada", "Lovelace")

    def test_parse_keeps_particles_with_surname(self):
        assert parse_name("Ludwig van Beethoven") == ("Ludwig", "van Beethoven")

    def test_parse_handles_suffix(self):
        given, family = parse_name("Martin Luther King Jr.")
        assert given == "Martin Luther"
        assert family.startswith("King")

    def test_corporate_names(self):
        assert is_corporate("{NVIDIA}")
        assert is_corporate("{Mozilla Foundation}")
        assert not is_corporate("Ada Lovelace")

    def test_detects_initials(self):
        assert looks_like_initials("J.")
        assert looks_like_initials("J. K.")
        assert looks_like_initials("JK")
        assert not looks_like_initials("Jinyu")
        assert not looks_like_initials("")

    def test_middle_initial_is_not_an_abbreviated_name(self):
        # "Aidan N. Gomez" is how the author publishes; only a missing *first*
        # name is a defect worth reporting.
        assert not looks_like_initials("Aidan N.")
        assert not looks_like_initials("Matthew S.")

    def test_format_name_orders(self):
        assert format_name("Ada", "Lovelace") == "Ada Lovelace"
        assert format_name("Ada", "Lovelace", "last-first") == "Lovelace, Ada"
        assert format_name("", "Lovelace") == "Lovelace"


class TestPages:
    def test_normalizes_single_dash(self):
        assert normalize_pages("745-777") == "745--777"

    def test_normalizes_en_dash(self):
        assert normalize_pages("181–214") == "181--214"

    def test_keeps_double_dash(self):
        assert normalize_pages("1--6") == "1--6"

    def test_collapses_identical_range(self):
        assert normalize_pages("42-42") == "42"

    def test_passes_through_unusual(self):
        assert normalize_pages("e1234") == "e1234"

    def test_empty(self):
        assert normalize_pages("") == ""
        assert normalize_pages(None) == ""


class TestIdentifiers:
    def test_year_from_noise(self):
        assert normalize_year("2021") == "2021"
        assert normalize_year("March 2019") == "2019"
        assert normalize_year(1984) == "1984"
        assert normalize_year("n.d.") == ""

    def test_doi_from_field(self):
        assert extract_doi("10.1109/TASLP.2014.2339736") == "10.1109/TASLP.2014.2339736"

    def test_doi_case_is_preserved(self):
        # DOIs resolve case-insensitively but citations should keep the
        # publisher's registered form.
        assert extract_doi("10.1109/ICASSP.2015.7178964") == "10.1109/ICASSP.2015.7178964"

    def test_doi_strips_trailing_punctuation(self):
        assert extract_doi("see 10.1162/neco.1991.3.1.79.") == "10.1162/neco.1991.3.1.79"

    def test_doi_from_url(self):
        assert extract_doi("https://doi.org/10.1162/neco.1994.6.2.181") == (
            "10.1162/neco.1994.6.2.181"
        )

    def test_doi_from_wrapped_note(self):
        assert extract_doi(r"\url{https://doi.org/10.5555/abc}") == "10.5555/abc"

    def test_doi_absent(self):
        assert extract_doi("no identifier here") is None

    def test_arxiv_from_eprint(self):
        assert extract_arxiv_id("2103.16716") == "2103.16716"

    def test_arxiv_from_url(self):
        assert extract_arxiv_id("https://arxiv.org/abs/1807.03819") == "1807.03819"

    def test_arxiv_strips_version(self):
        assert extract_arxiv_id("https://arxiv.org/abs/2006.11477v3") == "2006.11477"

    def test_arxiv_from_journal_field(self):
        assert extract_arxiv_id("arXiv preprint arXiv:1912.06670") == "1912.06670"

    def test_arxiv_old_style_id(self):
        assert extract_arxiv_id("https://arxiv.org/abs/cs.CL/9901001") == "cs.CL/9901001"

    def test_arxiv_absent(self):
        assert extract_arxiv_id("10.1109/ICASSP.2015.7178964") is None
