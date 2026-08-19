"""Cohort assignment from SIC, and the identifier split it depends on.

Basin presents companies by ticker and keys them by CIK. These tests pin the
behaviours that make that split safe -- picking one ticker out of several listed
on a filer, and preserving an assignment when a caller that knows nothing about
cohorts writes the same company -- together with the SIC-to-cohort rules that
decide membership in the first place.
"""

from __future__ import annotations

import pytest

from basin.cohorts import EXCLUDED, SIC_OVERRIDES, cohort_for, is_operator, producing_sic
from basin.edgar.discovery import profile_from_submissions
from basin.edgar.tickers import primary_ticker, ticker_map_from_payload
from basin.store import connect, queries
from basin.store.db import upsert_company


def _profile(**overrides):
    payload = {
        "cik": 1090012,
        "name": "TEST ENERGY CORP",
        "tickers": ["TST"],
        "exchanges": ["NYSE"],
        "sic": "1311",
        "sicDescription": "Crude Petroleum & Natural Gas",
        "addresses": {"business": {"stateOrCountry": "TX", "stateOrCountryDescription": "TX"}},
        "filings": {"recent": {"form": [], "filingDate": [], "accessionNumber": []}},
    }
    payload.update(overrides)
    return profile_from_submissions(payload)


class TestPrimaryTicker:
    def test_prefers_common_stock_over_warrant(self):
        # Occidental's CIK carries OXY and OXY-WT. The warrant is not the
        # company; picking it would label every Oxy fact with a derivative.
        assert primary_ticker(["OXY", "OXY-WT"]) == "OXY"
        assert primary_ticker(["ANNA", "ANNAW"]) == "ANNA"
        assert primary_ticker(["TBN", "TBNRL"]) == "TBN"

    def test_prefers_common_over_preferred_share_class(self):
        # Petrobras lists common and preferred ADRs against one registrant.
        assert primary_ticker(["PBR", "PBR-A"]) == "PBR"

    def test_falls_back_when_only_a_derivative_is_listed(self):
        # Reporting *a* ticker beats reporting none for a filer whose only
        # listed security is a warrant.
        assert primary_ticker(["ANNAW"]) == "ANNAW"

    def test_no_listing_is_none_not_an_error(self):
        # A delisted filer still files, still carries facts, and still has to
        # be citable -- so absence is a value, not a failure.
        assert primary_ticker([]) is None

    def test_choice_is_deterministic(self):
        assert primary_ticker(["BBB", "AAA"]) == primary_ticker(["AAA", "BBB"])


class TestTickerMap:
    def test_collects_every_ticker_on_one_cik(self):
        payload = {
            "0": {"cik_str": 797468, "ticker": "OXY", "title": "OCCIDENTAL"},
            "1": {"cik_str": 797468, "ticker": "OXY-WT", "title": "OCCIDENTAL"},
            "2": {"cik_str": 1539838, "ticker": "FANG", "title": "DIAMONDBACK"},
        }
        tm = ticker_map_from_payload(payload)
        assert tm.by_cik["0000797468"] == ("OXY", "OXY-WT")
        assert tm.primary("797468") == "OXY"
        assert tm.primary(1539838) == "FANG"

    def test_absent_cik_has_no_ticker(self):
        assert ticker_map_from_payload({}).primary("0000732834") is None


class TestSicCohorts:
    def test_crude_petroleum_is_the_ep_cohort(self):
        assert cohort_for(_profile()) == ("Oil & Gas E&P", "sic")

    def test_petroleum_refining_is_the_integrated_cohort(self):
        # The majors register as refiners. XOM, CVX, BP, SU and IMO are all
        # SIC 2911; none of them is a pure refiner.
        p = _profile(cik=93410, sic="2911", sicDescription="Petroleum Refining")
        assert cohort_for(p) == ("Oil & Gas Integrated", "sic")

    def test_royalty_traders_join_ep_but_are_not_operators(self):
        # 6792 is EDGAR's own code for royalty trusts. They publish full
        # reserve tables, so they are real E&P comparables -- but they lift
        # nothing, so a blank lifting cost is the business model, not a gap.
        p = _profile(cik=319655, name="SAN JUAN BASIN ROYALTY TRUST",
                     sic="6792", sicDescription="Oil Royalty Traders")
        assert cohort_for(p) == ("Oil & Gas E&P", "sic")
        assert not is_operator(p)

    def test_a_royalty_vehicle_filed_under_1311_is_caught_by_name(self):
        # EDGAR is inconsistent: Black Stone Minerals and Dorchester Minerals
        # are 1311, not 6792, so the name hint is still load-bearing.
        assert not is_operator(_profile(name="BLACK STONE MINERALS, L.P."))
        assert is_operator(_profile(name="DIAMONDBACK ENERGY, INC."))

    def test_a_non_producing_code_yields_no_cohort_and_says_why(self):
        # Midstream gathers third-party volumes under fee contracts: throughput,
        # not reserves. It must not land in a reserves panel.
        cohort, why = cohort_for(
            _profile(sic="4922", sicDescription="Natural Gas Transmission")
        )
        assert cohort is None
        assert "holds no reserves" in why

    def test_an_unrelated_code_yields_no_cohort(self):
        cohort, why = cohort_for(
            _profile(sic="2836", sicDescription="Biological Products")
        )
        assert cohort is None
        assert "2836" in why

    def test_a_stale_code_is_overridden_and_the_source_says_so(self):
        # ConocoPhillips is still coded 2911 Petroleum Refining, which it has
        # not been since it spun off Phillips 66 in 2012. Recording the source
        # as 'sic-override' is what keeps the deviation visible in the store.
        p = _profile(cik=1163165, name="CONOCOPHILLIPS", sic="2911",
                     sicDescription="Petroleum Refining")
        assert cohort_for(p) == ("Oil & Gas E&P", "sic-override")

    def test_every_override_targets_a_cohort_that_exists(self):
        cohorts = {"Oil & Gas E&P", "Oil & Gas Integrated"}
        for cik, (cohort, reason) in SIC_OVERRIDES.items():
            assert cohort in cohorts, cik
            # An override is a deviation from the SEC's own classification, so
            # it does not exist without a reason attached.
            assert len(reason) > 40, cik

    def test_overrides_and_exclusions_are_keyed_by_padded_cik(self):
        # Basin keys on CIK, and an unpadded key would silently never match.
        for cik in list(SIC_OVERRIDES) + list(EXCLUDED):
            assert len(cik) == 10 and cik.isdigit(), cik

    def test_no_filer_is_both_overridden_and_excluded(self):
        assert not set(SIC_OVERRIDES) & set(EXCLUDED)

    def test_producing_codes_are_the_ones_the_map_knows(self):
        assert producing_sic() == ("1311", "2911", "6792")


class TestCohortPersistence:
    @pytest.fixture
    def conn(self, tmp_path):
        return connect(tmp_path / "t.db")

    def test_cohort_survives_a_writer_that_knows_nothing_about_cohorts(self, conn):
        # ingest_xbrl upserts the company from the companyfacts payload, which
        # carries no ticker and no cohort. Assignment must not be a casualty.
        with conn:
            upsert_company(
                conn, "0000797468", "OCCIDENTAL PETROLEUM CORP", ticker="OXY",
                cohort="Oil & Gas E&P", cohort_source="sic",
                cohort_as_of="2026-08-19", country="USA", is_operator=False,
            )
            upsert_company(conn, "0000797468", "Occidental Petroleum Corporation")

        row = conn.execute("SELECT * FROM company WHERE cik = '0000797468'").fetchone()
        assert row["name"] == "Occidental Petroleum Corporation"  # name does refresh
        assert row["ticker"] == "OXY"
        assert row["cohort"] == "Oil & Gas E&P"
        assert row["is_operator"] == 0

    def test_two_filers_cannot_share_a_ticker(self, conn):
        with conn:
            upsert_company(conn, "0000000001", "First Corp", ticker="AAA")
        with pytest.raises(Exception, match="UNIQUE"):
            with conn:
                upsert_company(conn, "0000000002", "Second Corp", ticker="AAA")

    def test_many_filers_may_have_no_ticker(self, conn):
        # 13 of the first 94 companies are delisted or private. The unique
        # index is partial so that absence does not collide with absence.
        with conn:
            upsert_company(conn, "0000000003", "Private One")
            upsert_company(conn, "0000000004", "Private Two")
        assert conn.execute(
            "SELECT COUNT(*) FROM company WHERE ticker IS NULL"
        ).fetchone()[0] == 2

    def test_cohorts_query_counts_membership_and_reach(self, conn):
        with conn:
            upsert_company(conn, "0000000005", "A", ticker="A", cohort="Oil & Gas E&P")
            upsert_company(conn, "0000000006", "B", ticker="B", cohort="Oil & Gas E&P")
            upsert_company(conn, "0000000007", "C", ticker="C", cohort="Oil & Gas Integrated")
        rows = queries.cohorts(conn)
        assert [r["cohort"] for r in rows] == ["Oil & Gas E&P", "Oil & Gas Integrated"]
        assert rows[0]["companies"] == 2
        assert rows[0]["with_facts"] == 0
