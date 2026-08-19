"""How a filer reports, on the two axes that answer different questions.

Taxonomy decides whether the Facts layer can read a filer at all. Regime decides
whether two numbers it reads can share a column. Conflating them would put a
Canadian 2P reserve figure next to an SEC proved figure, which is the most
misleading cell this product could produce.
"""

from __future__ import annotations

import pytest

from basin.facts.taxonomy import (
    IFRS,
    NI_51_101,
    SUBPART_1200,
    UNKNOWN,
    US_GAAP,
    annual_form,
    detect_disclosure_regime,
    detect_reporting_taxonomy,
    is_comparable_to_sec,
    reaches_reserves,
)


def _facts(**namespaces: int) -> dict:
    return {"facts": {ns: {f"Tag{i}": {} for i in range(n)}
                      for ns, n in namespaces.items()}}


class TestReportingTaxonomy:
    def test_reads_the_dominant_accounting_namespace(self):
        assert detect_reporting_taxonomy(_facts(**{"ifrs-full": 333}))[0] == IFRS
        assert detect_reporting_taxonomy(_facts(**{"us-gaap": 438}))[0] == US_GAAP

    def test_a_minority_namespace_does_not_flip_the_call(self):
        # Cenovus carries 288 ifrs-full tags and exactly one us-gaap tag.
        taxonomy, note = detect_reporting_taxonomy(_facts(**{"ifrs-full": 288, "us-gaap": 1}))
        assert taxonomy == IFRS
        assert "us-gaap:1" in note and "ifrs-full:288" in note

    def test_a_genuinely_mixed_filer_keeps_both_counts(self):
        # Petrobras: 397 ifrs-full against 266 us-gaap. Worth finding later.
        _, note = detect_reporting_taxonomy(_facts(**{"ifrs-full": 397, "us-gaap": 266}))
        assert "us-gaap:266" in note

    def test_metadata_namespaces_are_not_evidence(self):
        # dei/ffd/invest say nothing about which standards a filer reports under.
        taxonomy, _ = detect_reporting_taxonomy(_facts(dei=1, ffd=5, invest=1))
        assert taxonomy == UNKNOWN

    def test_missing_payload_is_unknown_not_a_crash(self):
        assert detect_reporting_taxonomy(None)[0] == UNKNOWN
        assert detect_reporting_taxonomy({})[0] == UNKNOWN

    def test_only_us_gaap_reaches_reserve_concepts(self):
        # Not a judgement on the filer: ifrs-full has no reserve concept at all,
        # so the disclosure is in the document rather than in the XBRL.
        assert reaches_reserves(US_GAAP)
        assert not reaches_reserves(IFRS)


class TestDisclosureRegime:
    def test_10k_and_20f_are_both_sec_definitions(self):
        # Form 20-F Item 4.D applies Subpart 1200, so an IFRS filer's reserves
        # are still on SEC definitions -- Shell and BP report them this way.
        assert detect_disclosure_regime(["10-K", "8-K"])[0] == SUBPART_1200
        assert detect_disclosure_regime(["20-F", "6-K"])[0] == SUBPART_1200

    def test_40f_is_canadian_definitions(self):
        regime, note = detect_disclosure_regime(["40-F", "6-K"])
        assert regime == NI_51_101
        assert "forecast prices" in note and "2P" in note

    def test_amendments_count_as_their_base_form(self):
        assert annual_form(["20-F/A"]) == "20-F"
        assert detect_disclosure_regime(["40-F/A"])[0] == NI_51_101

    def test_a_filer_that_migrated_reports_its_prevailing_form(self):
        assert annual_form(["40-F", "10-K", "10-K", "10-K"]) == "10-K"

    def test_no_annual_form_is_none_not_a_guess(self):
        regime, note = detect_disclosure_regime(["8-K", "4", "S-8"])
        assert regime is None
        assert "no annual report form" in note

    def test_only_sec_definitions_share_a_column(self):
        assert is_comparable_to_sec(SUBPART_1200)
        assert not is_comparable_to_sec(NI_51_101)
        assert not is_comparable_to_sec(None)


class TestAxesAreIndependent:
    @pytest.mark.parametrize(
        "namespaces,forms,taxonomy,regime,comparable",
        [
            # Shell: IFRS financials, SEC reserve definitions. Comparable.
            ({"ifrs-full": 333}, ["20-F"], IFRS, SUBPART_1200, True),
            # Cenovus: IFRS financials, Canadian reserve definitions. Not.
            ({"ifrs-full": 288}, ["40-F"], IFRS, NI_51_101, False),
            # Imperial Oil: foreign-domiciled but reports us-gaap on a 10-K.
            ({"us-gaap": 345}, ["10-K"], US_GAAP, SUBPART_1200, True),
        ],
    )
    def test_taxonomy_does_not_determine_comparability(
        self, namespaces, forms, taxonomy, regime, comparable
    ):
        assert detect_reporting_taxonomy(_facts(**namespaces))[0] == taxonomy
        assert detect_disclosure_regime(forms)[0] == regime
        assert is_comparable_to_sec(regime) is comparable
