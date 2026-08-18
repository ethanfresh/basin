from __future__ import annotations

import pytest

from basin.edgar.client import cik_padded
from basin.facts import concepts
from basin.facts.xbrl import (
    companyconcept_url,
    companyfacts_url,
    coverage_for_company,
    resolve_alias,
    rows_for_concept,
)


class TestCikPadding:
    def test_pads_to_ten_digits(self):
        assert cik_padded(1090012) == "0001090012"
        assert cik_padded("1090012") == "0001090012"

    def test_accepts_already_padded(self):
        assert cik_padded("0001090012") == "0001090012"

    def test_rejects_non_numeric(self):
        with pytest.raises(ValueError):
            cik_padded("DVN")


class TestUrls:
    def test_companyfacts_url_uses_padded_cik(self):
        assert companyfacts_url(1090012).endswith("/companyfacts/CIK0001090012.json")

    def test_companyconcept_url_includes_taxonomy_and_tag(self):
        url = companyconcept_url(1090012, "srt", "ProvedDevelopedReservesBOE1")
        assert url.endswith(
            "/companyconcept/CIK0001090012/srt/ProvedDevelopedReservesBOE1.json"
        )


class TestAliasResolution:
    def test_prefers_earlier_alias_when_both_taxonomies_present(self, companyfacts):
        # The live run showed filers tagging the same disclosure under both
        # srt and us-gaap; the preferred one must win deterministically.
        resolved = resolve_alias(companyfacts, concepts.RESERVES_DEVELOPED)
        assert resolved == ("srt", "ProvedDevelopedReservesBOE1")

    def test_falls_through_to_later_alias(self, companyfacts):
        resolved = resolve_alias(companyfacts, concepts.CAPEX)
        assert resolved == ("us-gaap", "PaymentsToAcquireOilAndGasProperty")

    def test_returns_none_when_untagged(self, companyfacts):
        assert resolve_alias(companyfacts, concepts.AVERAGE_SALES_PRICE) is None


class TestRowExtraction:
    def test_ignores_a_rival_tag_name(self, companyfacts):
        # ProvedDevelopedReservesVolume is present but lower in the order.
        rows = rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED)
        assert {r.tag for r in rows} == {"ProvedDevelopedReservesBOE1"}
        assert {r.unit for r in rows} == {"MMBoe"}

    def test_merges_the_same_tag_across_taxonomies(self, companyfacts):
        # One series split by a taxonomy migration; dropping either half
        # silently truncates the company's history.
        rows = rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED)
        assert {r.taxonomy for r in rows} == {"srt", "us-gaap"}
        assert "2021-12-31" in {r.period_end for r in rows}

    def test_filters_to_requested_forms(self, companyfacts):
        rows = rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED)
        assert {r.form for r in rows} == {"10-K"}
        assert [r.period_end for r in rows] == [
            "2021-12-31", "2023-12-31", "2024-12-31",
        ]

    def test_all_forms_when_filter_disabled(self, companyfacts):
        rows = rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED, forms=None)
        assert "10-Q" in {r.form for r in rows}

    def test_drops_observations_without_an_accession(self, companyfacts):
        # A value with no accession cannot be cited, so it is not storable.
        rows = rows_for_concept(companyfacts, concepts.CAPEX)
        assert len(rows) == 1
        assert rows[0].accession == "0001090012-25-000010"

    def test_duration_facts_carry_their_start(self, companyfacts):
        capex = rows_for_concept(companyfacts, concepts.CAPEX)[0]
        reserves = rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED)[0]
        assert capex.is_duration
        assert not reserves.is_duration

    def test_rows_are_marked_as_xbrl_provenance(self, companyfacts):
        rows = rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED)
        assert all(r.extracted_by == "xbrl" for r in rows)
        assert all(r.cik == "0001090012" for r in rows)

    def test_untagged_concept_yields_no_rows(self, companyfacts):
        assert rows_for_concept(companyfacts, concepts.STANDARDIZED_MEASURE) == []


class TestCoverage:
    def test_reports_tagged_and_untagged(self, companyfacts, monkeypatch):
        monkeypatch.setattr(
            "basin.facts.xbrl.fetch_companyfacts", lambda client, cik: companyfacts
        )
        coverage = coverage_for_company(None, 1090012)
        by_key = {c.concept_key: c for c in coverage.concepts}

        assert by_key["proved_developed_reserves_boe"].tagged
        assert by_key["proved_developed_reserves_boe"].taxonomy == "srt"
        assert by_key["proved_developed_reserves_boe"].latest_period_end == "2024-12-31"
        assert not by_key["standardized_measure"].tagged

    def test_missing_payload_reports_rather_than_raises(self, monkeypatch):
        from basin.edgar.client import NotFound

        def boom(client, cik):
            raise NotFound("404")

        monkeypatch.setattr("basin.facts.xbrl.fetch_companyfacts", boom)
        coverage = coverage_for_company(None, 999)

        assert coverage.error
        assert coverage.tagged_count == 0
