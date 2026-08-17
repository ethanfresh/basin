"""The store's job is to make un-citable and history-destroying writes impossible."""

from __future__ import annotations

import sqlite3

import pytest

from basin.facts.xbrl import FactRow, rows_for_concept
from basin.facts import concepts
from basin.store import connect, insert_facts, record_filing, upsert_company


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "test.db")
    upsert_company(connection, "0001090012", "TEST ENERGY CORP", ticker="TST")
    record_filing(
        connection, "0001090012-25-000010", "0001090012", "10-K", "2025-02-20"
    )
    record_filing(
        connection, "0001090012-24-000010", "0001090012", "10-K", "2024-02-20"
    )
    yield connection
    connection.close()


def _row(**overrides) -> FactRow:
    base = dict(
        cik="0001090012",
        concept_key="proved_developed_reserves_boe",
        taxonomy="srt",
        tag="ProvedDevelopedReservesBOE1",
        value=1200.0,
        unit="MMBoe",
        period_start=None,
        period_end="2024-12-31",
        fiscal_year=2024,
        fiscal_period="FY",
        accession="0001090012-25-000010",
        form="10-K",
        filed="2025-02-20",
    )
    base.update(overrides)
    return FactRow(**base)


class TestInsert:
    def test_writes_rows(self, conn, companyfacts):
        rows = rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED)
        assert insert_facts(conn, rows) == 2

    def test_reingesting_the_same_filing_is_idempotent(self, conn):
        assert insert_facts(conn, [_row()]) == 1
        assert insert_facts(conn, [_row()]) == 0
        assert conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0] == 1

    def test_restatement_appends_rather_than_overwrites(self, conn):
        insert_facts(conn, [_row()])
        # Same company, concept and period; new filing, different value.
        insert_facts(
            conn, [_row(value=1180.0, accession="0001090012-24-000010")]
        )
        values = [
            r[0]
            for r in conn.execute(
                "SELECT value FROM fact WHERE period_end='2024-12-31' ORDER BY value"
            )
        ]
        assert values == [1180.0, 1200.0], "prior value must survive a restatement"

    def test_fact_without_a_registered_filing_is_rejected(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            insert_facts(conn, [_row(accession="0009999999-99-000001")])


class TestCitationConstraint:
    def test_llm_fact_requires_a_source_span(self, conn):
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                INSERT INTO fact (cik, concept_key, value, unit, period_end,
                                  accession, form, extracted_by)
                VALUES ('0001090012', 'production_cost_per_boe', 11.5, 'USD/boe',
                        '2024-12-31', '0001090012-25-000010', '10-K', 'llm:test')
                """
            )

    def test_llm_fact_with_a_source_span_is_accepted(self, conn):
        conn.execute(
            """
            INSERT INTO fact (cik, concept_key, value, unit, period_end,
                              accession, form, extracted_by, section, source_span)
            VALUES ('0001090012', 'production_cost_per_boe', 11.5, 'USD/boe',
                    '2024-12-31', '0001090012-25-000010', '10-K', 'llm:test',
                    'Item 7 MD&A', 'Lease operating expense per Boe was $11.50')
            """
        )
        assert conn.execute("SELECT COUNT(*) FROM fact").fetchone()[0] == 1

    def test_xbrl_fact_needs_no_source_span(self, conn):
        assert insert_facts(conn, [_row()]) == 1


class TestCurrentView:
    def test_view_returns_the_latest_filed_value(self, conn):
        insert_facts(conn, [_row(value=1180.0, accession="0001090012-24-000010")])
        insert_facts(conn, [_row(value=1200.0)])
        rows = conn.execute(
            "SELECT value, accession FROM fact_current WHERE period_end='2024-12-31'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == 1200.0
        assert rows[0]["accession"] == "0001090012-25-000010"
