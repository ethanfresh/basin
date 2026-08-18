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
    # The earlier-taxonomy half of the developed-reserves series.
    record_filing(
        connection, "0001090012-22-000010", "0001090012", "10-K", "2022-02-20"
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
        # Two srt periods plus the one carried under the older taxonomy.
        rows = rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED)
        assert insert_facts(conn, rows) == 3

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


class TestProductIsPartOfTheCell:
    """Oil and gas facts share a concept but are not the same cell."""

    def test_products_coexist_rather_than_evicting_each_other(self, conn):
        insert_facts(
            conn,
            [
                _row(
                    concept_key="average_sales_price",
                    value=76.5,
                    unit="USD/bbl",
                    product="oil",
                ),
                _row(
                    concept_key="average_sales_price",
                    value=2.35,
                    unit="USD/MMBTU",
                    product="gas",
                ),
            ],
        )
        rows = conn.execute(
            """SELECT product, value FROM fact_current
               WHERE concept_key='average_sales_price' ORDER BY product"""
        ).fetchall()
        assert [(r["product"], r["value"]) for r in rows] == [
            ("gas", 2.35),
            ("oil", 76.5),
        ]

    def test_same_quantity_two_units_resolves_by_unit_rank(self, conn):
        # Devon tags proved reserves as both MMBoe and MMcfe. Both are stored;
        # the canonical unit is the one that reaches the cell.
        insert_facts(
            conn,
            [
                _row(concept_key="proved_reserves_boe", value=1200.0,
                     unit="MMBoe", unit_rank=0),
                _row(concept_key="proved_reserves_boe", value=7200.0,
                     unit="MMcfe", unit_rank=7),
            ],
        )
        rows = conn.execute(
            "SELECT unit, value FROM fact_current WHERE concept_key='proved_reserves_boe'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["unit"] == "MMBoe"

    def test_restatement_still_wins_over_unit_rank(self, conn):
        # Filing recency outranks unit preference: a newer filing's value is
        # the current one even if an older filing used the canonical unit.
        insert_facts(
            conn,
            [
                _row(concept_key="proved_reserves_boe", value=1100.0, unit="MMBoe",
                     unit_rank=0, accession="0001090012-24-000010"),
                _row(concept_key="proved_reserves_boe", value=7200.0, unit="MMcfe",
                     unit_rank=7, accession="0001090012-25-000010"),
            ],
        )
        rows = conn.execute(
            "SELECT unit, value FROM fact_current WHERE concept_key='proved_reserves_boe'"
        ).fetchall()
        assert len(rows) == 1
        assert rows[0]["value"] == 7200.0

    def test_one_row_per_cell(self, conn, companyfacts):
        from basin.facts import concepts
        from basin.facts.xbrl import rows_for_concept

        insert_facts(conn, rows_for_concept(companyfacts, concepts.RESERVES_DEVELOPED))
        dupes = conn.execute(
            """SELECT COUNT(*) FROM (
                   SELECT 1 FROM fact_current
                   GROUP BY cik, concept_key, COALESCE(product,''), period_end,
                            COALESCE(period_start,'')
                   HAVING COUNT(*) > 1)"""
        ).fetchone()[0]
        assert dupes == 0


class TestCollisionsAreSurfaced:
    def test_conflicting_values_in_one_filing_are_reported(self, conn):
        insert_facts(
            conn,
            [
                _row(concept_key="capex", value=3_500_000_000.0, unit="USD"),
                _row(concept_key="capex", value=3_900_000_000.0, unit="USD",
                     period_start="2024-01-01"),
            ],
        )
        # Different period_start makes these different cells, not a collision.
        assert conn.execute("SELECT COUNT(*) FROM fact_collision").fetchone()[0] == 0
