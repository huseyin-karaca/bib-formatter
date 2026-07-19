from bibformatter.venues import canonicalize, extract_acronym, strip_boilerplate


class TestAcronymExtraction:
    def test_finds_trailing_acronym(self):
        assert extract_acronym("Proc. Int. Conf. Mach. Learn. (ICML)") == "ICML"

    def test_finds_acronym_with_year(self):
        assert extract_acronym("... Environments (CHiME 2023)") == "CHiME"

    def test_finds_acronym_glued_to_year(self):
        assert extract_acronym("... Pattern Recognition (ICPR2012)") == "ICPR"

    def test_ignores_ordinary_parentheses(self):
        assert extract_acronym("Some Conference (Poster Session)") is None

    def test_none_when_absent(self):
        assert extract_acronym("Neural Computation") is None


class TestBoilerplate:
    def test_strips_proc_of_the_ordinal(self):
        assert strip_boilerplate("Proceedings of the 35th International Conference on X") == (
            "International Conference on X"
        )

    def test_keeps_international_and_annual(self):
        # These are part of the venue's real name, not boilerplate.
        assert strip_boilerplate("Proc. Annual Meeting of the ACL").startswith("Annual")

    def test_strips_leading_year(self):
        assert "2015" not in strip_boilerplate(
            "2015 IEEE International Conference on Acoustics"
        )

    def test_strips_trailing_proceedings(self):
        assert not strip_boilerplate(
            "IEEE Workshop on Speech Proceedings"
        ).endswith("Proceedings")


class TestCanonicalize:
    def test_expands_abbreviated_conference(self):
        display, acronym, kind = canonicalize("Proc. Int. Conf. Mach. Learn. (ICML)")
        assert display == "International Conference on Machine Learning (ICML)"
        assert acronym == "ICML"
        assert kind == "conference"

    def test_matches_full_name_without_acronym(self):
        display, acronym, _ = canonicalize("International Conference on Learning Representations")
        assert display == "International Conference on Learning Representations (ICLR)"
        assert acronym == "ICLR"

    def test_normalizes_nips_to_neurips(self):
        display, acronym, _ = canonicalize("Advances in Neural Information Processing Systems")
        assert acronym == "NeurIPS"
        assert display.endswith("(NeurIPS)")

    def test_bare_acronym(self):
        display, acronym, _ = canonicalize("JMLR")
        assert acronym == "JMLR"
        assert display == "Journal of Machine Learning Research (JMLR)"

    def test_interspeech_variants_agree(self):
        first, _, _ = canonicalize("Proc. Interspeech 2019")
        second, _, _ = canonicalize("Interspeech 2015")
        third, _, _ = canonicalize("Proceedings of Interspeech 2022")
        assert first == second == third

    def test_icassp_variants_agree(self):
        first, _, _ = canonicalize(
            "2015 IEEE International Conference on Acoustics, Speech and Signal Processing (ICASSP)"
        )
        second, _, _ = canonicalize(
            "ICASSP 2022 - 2022 IEEE International Conference on Acoustics, "
            "Speech and Signal Processing (ICASSP)"
        )
        assert first == second

    def test_expands_abbreviated_journal_without_acronym(self):
        display, acronym, kind = canonicalize("Neural Comput.")
        assert display == "Neural Computation"
        assert acronym is None
        assert kind == "journal"

    def test_arxiv_is_not_a_venue(self):
        display, _, kind = canonicalize("arXiv preprint arXiv:1912.06670")
        assert display == ""
        assert kind == "preprint"

    def test_unknown_venue_is_kept(self):
        display, _, _ = canonicalize("Journal of Extremely Niche Studies")
        assert display == "Journal of Extremely Niche Studies"

    def test_unknown_venue_keeps_its_acronym(self):
        display, acronym, _ = canonicalize("Some New Workshop on Things (SNWT)")
        assert acronym == "SNWT"
        assert display.endswith("(SNWT)")

    def test_config_supplied_acronym(self):
        display, acronym, _ = canonicalize(
            "International Workshop on Widget Science",
            extra_acronyms={"International Workshop on Widget Science": "IWWS"},
        )
        assert acronym == "IWWS"
        assert display.endswith("(IWWS)")

    def test_append_acronym_can_be_disabled(self):
        display, acronym, _ = canonicalize(
            "International Conference on Machine Learning", append_acronym=False
        )
        assert display == "International Conference on Machine Learning"
        assert acronym == "ICML"

    def test_empty_input(self):
        assert canonicalize("") == ("", None, None)
        assert canonicalize(None) == ("", None, None)
