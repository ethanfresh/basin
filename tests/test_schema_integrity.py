"""Guards against a class of failure that produces wrong answers silently.

The identity index was once an expression index over ``COALESCE(product,'')``.
Under SQLite 3.41 a query plan that scanned it returned NULL for ``value``, so
``COUNT(DISTINCT value) > 1`` matched nothing: the same query answered 240 or
0 depending on which plan the optimiser chose. Nothing raised; the numbers
were just wrong.

These tests pin the property that matters -- the answer does not depend on the
plan -- rather than the implementation that currently delivers it.
"""

from __future__ import annotations

import pytest

from basin.facts.xbrl import FactRow
from basin.store import connect, insert_facts, record_filing, upsert_company


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "integrity.db")
    upsert_company(connection, "0001090012", "TEST ENERGY CORP", ticker="TST")
    for n, date in [("25-000010", "2025-02-20"), ("24-000010", "2024-02-20")]:
        record_filing(connection, f"0001090012-{n}", "0001090012", "10-K", date)
    yield connection
    connection.close()


def _row(**kw) -> FactRow:
    base = dict(
        cik="0001090012",
        concept_key="proved_reserves_boe",
        taxonomy="srt",
        tag="ProvedDevelopedAndUndevelopedReservesNet",
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
    base.update(kw)
    return FactRow(**base)


class TestPlanIndependence:
    """The same question must get the same answer under any query plan."""

    def test_aggregate_over_fact_is_plan_independent(self, conn):
        insert_facts(
            conn,
            [
                _row(value=1200.0),
                _row(value=1180.0, accession="0001090012-24-000010"),
            ],
        )
        sql = """
            SELECT COUNT(*) FROM (
                SELECT 1 FROM fact {hint}
                GROUP BY cik, concept_key, period_end, product_key, unit
                HAVING COUNT(DISTINCT value) > 1)
        """
        with_index = conn.execute(sql.format(hint="")).fetchone()[0]
        without_index = conn.execute(sql.format(hint="NOT INDEXED")).fetchone()[0]
        assert with_index == without_index == 1

    def test_value_is_readable_when_scanning_the_identity_index(self, conn):
        insert_facts(conn, [_row(value=1200.0)])
        # Forcing the index is the exact condition that used to null out value.
        row = conn.execute(
            "SELECT value, unit FROM fact INDEXED BY fact_identity_idx"
        ).fetchone()
        assert row["value"] == 1200.0
        assert row["unit"] == "MMBoe"


class TestGeneratedKeys:
    def test_null_product_and_period_start_become_empty_strings(self, conn):
        insert_facts(conn, [_row()])
        row = conn.execute(
            "SELECT product, product_key, period_start, period_start_key FROM fact"
        ).fetchone()
        assert row["product"] is None and row["product_key"] == ""
        assert row["period_start"] is None and row["period_start_key"] == ""

    def test_identity_still_dedupes_rows_with_null_product(self, conn):
        # The original bug this index exists to prevent: NULL != NULL, so a
        # plain UNIQUE over the nullable columns would not deduplicate.
        assert insert_facts(conn, [_row()]) == 1
        assert insert_facts(conn, [_row()]) == 0


class TestReserveConsistency:
    def _pair(self, conn, dev, dev_unit, tot, tot_unit):
        insert_facts(
            conn,
            [
                _row(concept_key="proved_developed_reserves_boe", value=dev, unit=dev_unit),
                _row(concept_key="proved_reserves_boe", value=tot, unit=tot_unit),
            ],
        )
        return conn.execute(
            "SELECT issue, ratio FROM reserve_consistency"
        ).fetchone()

    def test_normal_pair_has_no_issue(self, conn):
        row = self._pair(conn, 700.0, "MMBoe", 1000.0, "MMBoe")
        assert row["issue"] is None
        assert row["ratio"] == pytest.approx(0.7)

    def test_developed_exceeding_total_is_flagged(self, conn):
        assert self._pair(conn, 1300.0, "MMBoe", 1000.0, "MMBoe")["issue"] == (
            "developed exceeds total"
        )

    def test_mismatched_units_are_flagged(self, conn):
        row = self._pair(conn, 700.0, "MMBoe", 6000.0, "MMcfe")
        assert row["issue"] == "units differ"
        # No ratio, because dividing across units would invent a number.
        assert row["ratio"] is None

    def test_developed_equal_to_total_is_flagged(self, conn):
        assert self._pair(conn, 1000.0, "MMBoe", 1000.0, "MMBoe")["issue"] == (
            "developed equals total"
        )

    def test_rounding_noise_is_not_flagged(self, conn):
        # A 1% overshoot is rounding between two tables in one filing, not a
        # contradiction worth a caveat on the cell.
        assert self._pair(conn, 1010.0, "MMBoe", 1000.0, "MMBoe")["issue"] is None
