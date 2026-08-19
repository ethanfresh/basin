"""Cohort assignment, and the identifier split it depends on.

Basin presents companies by ticker and keys them by CIK. These tests pin the
behaviours that make that split safe -- picking one ticker out of several listed
on a filer, refusing to widen a cohort, and preserving an assignment when a
caller that knows nothing about cohorts writes the same company.
"""

from __future__ import annotations

import pytest

from basin.edgar.tickers import primary_ticker, ticker_map_from_payload
from basin.finviz import FinvizError, parse_export
from basin.store import connect, queries
from basin.store.db import upsert_company

HEADER = '"No.","Ticker","Company","Sector","Industry","Country","Market Cap"\n'


def _csv(*rows: str) -> str:
    return HEADER + "".join(rows)


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


class TestParseExport:
    def test_reads_a_screener_row(self):
        rows = parse_export(_csv('1,"APA","APA Corp","Energy","Oil & Gas E&P","USA",15548.60\n'))
        assert rows[0].ticker == "APA"
        assert rows[0].industry == "Oil & Gas E&P"
        assert rows[0].market_cap == pytest.approx(15548.60)
        assert rows[0].is_usa

    def test_foreign_domicile_is_flagged(self):
        # A US-listed filer domiciled abroad files 20-F/40-F under IFRS, which
        # the Facts layer cannot read -- so domicile predicts reachability.
        rows = parse_export(_csv('1,"SHEL","Shell plc","Energy","Oil & Gas Integrated","United Kingdom",0\n'))
        assert not rows[0].is_usa

    def test_rejects_a_slug_that_returns_the_wrong_industry(self):
        # A filter slug that silently widened would contaminate a cohort with
        # companies whose metrics do not apply, which is the one failure the
        # cohort split exists to prevent.
        with pytest.raises(FinvizError, match="filter slug has changed meaning"):
            parse_export(
                _csv('1,"APA","APA Corp","Energy","Oil & Gas E&P","USA",15548.60\n'),
                expected_industry="Uranium",
            )

    def test_missing_market_cap_is_none_not_zero(self):
        rows = parse_export(_csv('1,"XYZ","Xyz","Energy","Oil & Gas E&P","USA",-\n'))
        assert rows[0].market_cap is None


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
                cohort="Oil & Gas E&P", cohort_source="finviz",
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
