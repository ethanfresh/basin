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

import sqlite3

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


class TestReserveProductAxis:
    """The check pairs like with like, or it is not a check at all."""

    def test_a_product_is_tested_against_its_own_total(self, conn):
        # A filer splitting both concepts by product used to produce every
        # pairing of the two, so oil developed was tested against gas total:
        # nine rows from three products, seven of them comparing quantities
        # that have no arithmetic relationship.
        rows = []
        for product, developed, total in [
            ("oil", 700.0, 1000.0), ("gas", 300.0, 400.0), ("ngl", 100.0, 150.0),
        ]:
            rows += [
                _row(concept_key="proved_developed_reserves_boe",
                     product=product, value=developed),
                _row(concept_key="proved_reserves_boe", product=product, value=total),
            ]
        insert_facts(conn, rows)

        pairs = conn.execute(
            "SELECT product, developed_value, total_value FROM reserve_consistency"
        ).fetchall()
        assert len(pairs) == 3
        assert {(r["product"], r["developed_value"], r["total_value"]) for r in pairs} == {
            ("oil", 700.0, 1000.0), ("gas", 300.0, 400.0), ("ngl", 100.0, 150.0),
        }

    def test_a_split_filer_is_not_flagged_by_the_split_alone(self, conn):
        # Every product here is internally coherent, so nothing should be
        # reported. Cross-product pairing flagged this shape as 'developed
        # exceeds total' six times over.
        rows = []
        for product in ("oil", "gas", "ngl"):
            rows += [
                _row(concept_key="proved_developed_reserves_boe",
                     product=product, value=700.0),
                _row(concept_key="proved_reserves_boe", product=product, value=1000.0),
            ]
        insert_facts(conn, rows)
        assert conn.execute(
            "SELECT COUNT(*) FROM reserve_consistency WHERE issue IS NOT NULL"
        ).fetchone()[0] == 0


class TestViewsFollowTheSchemaFile:
    """A view holds no data, so the file is the only definition that counts."""

    def test_an_edited_view_reaches_a_store_that_already_exists(self, tmp_path):
        path = tmp_path / "stale.db"
        connect(path).close()
        with sqlite3.connect(path) as raw:
            raw.execute("DROP VIEW reserve_consistency")
            raw.execute(
                "CREATE VIEW reserve_consistency AS SELECT 1 AS cik, 2 AS issue"
            )

        # CREATE VIEW IF NOT EXISTS would leave the stand-in in place, and the
        # corrected definition would reach new databases only.
        conn = connect(path)
        columns = {r[1] for r in conn.execute("PRAGMA table_info(reserve_consistency)")}
        assert "developed_value" in columns and "product" in columns
        conn.close()


class TestReserveSumCheck:
    """developed + undeveloped = total is what catches a wrong alias choice."""

    def _triple(self, conn, dev, undev, total, unit="MMBoe"):
        rows = [
            _row(concept_key="proved_developed_reserves_boe", value=dev, unit=unit),
            _row(concept_key="proved_reserves_boe", value=total, unit=unit),
        ]
        if undev is not None:
            rows.append(
                _row(concept_key="proved_undeveloped_reserves_boe", value=undev, unit=unit)
            )
        insert_facts(conn, rows)
        return conn.execute("SELECT issue FROM reserve_consistency").fetchone()["issue"]

    def test_components_summing_to_total_is_clean(self, conn):
        assert self._triple(conn, 700.0, 300.0, 1000.0) is None

    def test_components_disagreeing_with_total_is_flagged(self, conn):
        # Continental's shape: the two components agree with each other and
        # the total is a third of their sum, so the total's tag is wrong.
        # 'developed exceeds total' is also true here, but the sum mismatch is
        # the diagnosis that says which of the three to distrust.
        assert self._triple(conn, 960.0, 1825.0, 865.0) == (
            "components do not sum to total"
        )

    def test_sum_mismatch_without_developed_exceeding_total(self, conn):
        # Developed alone stays under the total, so only the sum check fires.
        assert self._triple(conn, 700.0, 900.0, 1000.0) == (
            "components do not sum to total"
        )

    def test_small_sum_discrepancy_is_tolerated(self, conn):
        # 2% is rounding between two tables in one filing.
        assert self._triple(conn, 700.0, 320.0, 1000.0) is None

    def test_missing_undeveloped_skips_the_sum_check(self, conn):
        assert self._triple(conn, 700.0, None, 1000.0) is None


class TestAliasValidation:
    """Alias choice is measured against the filer's own arithmetic."""

    @staticmethod
    def _payload(dev, undev, total, unit="MBoe", periods=("2024-12-31",)):
        def series(tag, values):
            return {tag: {"units": {unit: [
                {"end": p, "val": v, "accn": f"0001-25-{i:06d}",
                 "form": "10-K", "filed": f"{int(p[:4]) + 1}-02-20"}
                for i, (p, v) in enumerate(zip(periods, values))
            ]}}}
        facts = {"srt": {}}
        facts["srt"].update(series("ProvedDevelopedReservesBOE1", dev))
        facts["srt"].update(series("ProvedUndevelopedReserveBOE1", undev))
        facts["srt"].update(series("ProvedDevelopedAndUndevelopedReserveNetEnergy", total))
        return {"cik": 1090012, "facts": facts}

    def test_coherent_family_validates(self):
        from basin.facts.validation import validate_reserve_family

        v = validate_reserve_family(self._payload([700.0], [300.0], [1000.0]))
        assert v.status == "validated"
        assert v.coherent_periods == 1

    def test_family_that_never_agrees_is_incoherent(self):
        from basin.facts.validation import validate_reserve_family

        v = validate_reserve_family(self._payload([960.0], [1825.0], [865.0]))
        assert v.status == "incoherent"
        assert "no combination" in v.note

    def test_family_that_stops_agreeing_is_drifted(self):
        # Continental's shape: the identity holds for years, then stops.
        # Averaging over history would call this validated and put a wrong
        # number in the panel's most recent column.
        from basin.facts.validation import validate_reserve_family

        v = validate_reserve_family(
            self._payload(
                [500.0, 700.0, 960.0],
                [500.0, 300.0, 1825.0],
                [1000.0, 1000.0, 865.0],
                periods=("2022-12-31", "2023-12-31", "2024-12-31"),
            )
        )
        assert v.status == "drifted"
        assert v.coherent_periods == 2 and v.tested_periods == 3

    def test_missing_concept_is_insufficient(self):
        from basin.facts.validation import validate_reserve_family

        payload = self._payload([700.0], [300.0], [1000.0])
        del payload["facts"]["srt"]["ProvedUndevelopedReserveBOE1"]
        v = validate_reserve_family(payload)
        assert v.status == "insufficient"
        assert v.choices == {}
