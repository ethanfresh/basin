"""Read queries — especially the ones that must not imply false comparability."""

from __future__ import annotations

import pytest

from basin.facts.xbrl import FactRow
from basin.store import (
    connect,
    insert_facts,
    record_filing,
    record_verification,
    upsert_company,
)
from basin.store.queries import filing_url, panel, panel_wide, summary, unit_groups


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "q.db")
    for cik, name, ticker in [
        ("0001539838", "Diamondback Energy, Inc.", "FANG"),
        ("0001090012", "Devon Energy Corp", "DVN"),
        ("0000717423", "Murphy Oil Corp", "MUR"),
    ]:
        upsert_company(connection, cik, name, ticker=ticker)
        record_filing(connection, f"{cik}-25-000010", cik, "10-K", "2025-02-20")
    yield connection
    connection.close()


def _row(cik, value, unit, **kw) -> FactRow:
    base = dict(
        cik=cik,
        concept_key="proved_developed_reserves_boe",
        taxonomy="srt",
        tag="ProvedDevelopedReservesBOE1",
        value=value,
        unit=unit,
        period_start=None,
        period_end="2024-12-31",
        fiscal_year=2024,
        fiscal_period="FY",
        accession=f"{cik}-25-000010",
        form="10-K",
        filed="2025-02-20",
    )
    base.update(kw)
    return FactRow(**base)


class TestCitationUrl:
    def test_builds_a_resolvable_sec_index_url(self):
        url = filing_url("0001539838", "0001539838-26-000010")
        assert url == (
            "https://www.sec.gov/Archives/edgar/data/1539838/"
            "000153983826000010/0001539838-26-000010-index.htm"
        )

    def test_strips_cik_padding_for_the_archive_path(self):
        # The Archives path uses the unpadded CIK; the padded form 404s.
        assert "/data/1090012/" in filing_url("0001090012", "0001090012-25-000010")


class TestPanelComparability:
    def test_rows_are_grouped_by_unit_not_ranked_across_units(self, conn):
        # Filers disagree about whether the value already carries the unit's
        # prefix, so a single ranking across units would sort by labelling
        # convention rather than by size.
        insert_facts(
            conn,
            [
                _row("0001539838", 2_521_028_000.0, "MBoe"),
                _row("0001090012", 2_155.0, "MMcfe"),
                _row("0000717423", 418_900_000.0, "MMBbls"),
            ],
        )
        rows = panel(conn, "proved_developed_reserves_boe", "2024-12-31")
        units = [r["unit"] for r in rows]
        # Units stay contiguous: no interleaving that implies a global order.
        assert units == sorted(units)

    def test_unit_groups_split_into_comparable_sets(self, conn):
        insert_facts(
            conn,
            [
                _row("0001539838", 2_521_028_000.0, "MBoe"),
                _row("0001090012", 900_000.0, "MBoe"),
                _row("0000717423", 418_900_000.0, "MMBbls"),
            ],
        )
        groups = unit_groups(panel(conn, "proved_developed_reserves_boe", "2024-12-31"))
        assert [g["unit"] for g in groups] == ["MBoe", "MMBbls"]
        assert groups[0]["count"] == 2
        # Largest group first, and sorted by magnitude inside the group.
        assert groups[0]["rows"][0]["value"] > groups[0]["rows"][1]["value"]

    def test_every_panel_row_carries_its_citation(self, conn):
        insert_facts(conn, [_row("0001539838", 100.0, "MBoe")])
        for row in panel(conn, "proved_developed_reserves_boe", "2024-12-31"):
            assert row["accession"]
            assert row["filing_url"].startswith("https://www.sec.gov/Archives/")

    def test_products_appear_as_separate_rows(self, conn):
        insert_facts(
            conn,
            [
                _row("0001539838", 76.5, "USD/bbl", concept_key="average_sales_price",
                     product="oil"),
                _row("0001539838", 2.35, "USD/MMBTU", concept_key="average_sales_price",
                     product="gas"),
            ],
        )
        rows = panel(conn, "average_sales_price", "2024-12-31")
        assert sorted(r["product"] for r in rows) == ["gas", "oil"]

    def test_product_filter_narrows_the_panel(self, conn):
        insert_facts(
            conn,
            [
                _row("0001539838", 76.5, "USD/bbl", concept_key="average_sales_price",
                     product="oil"),
                _row("0001539838", 2.35, "USD/MMBTU", concept_key="average_sales_price",
                     product="gas"),
            ],
        )
        rows = panel(conn, "average_sales_price", "2024-12-31", product="oil")
        assert len(rows) == 1
        assert rows[0]["product"] == "oil"


class TestSummary:
    def test_counts_the_dataset(self, conn):
        insert_facts(conn, [_row("0001539838", 100.0, "MBoe")])
        s = summary(conn)
        assert s["companies"] == 3
        assert s["facts"] == 1
        assert s["cells"] == 1
        assert s["latest_period"] == "2024-12-31"


class TestPanelVerdicts:
    """A cell carries the verdict, not just whether it was favourable.

    The panel colours a figure checked against its filing and not located
    differently from one nothing has checked yet. Collapsing both to "not
    verified" told a reader that unfinished work and a dead end were the same
    finding.
    """

    def test_a_cell_carries_the_verification_verdict(self, conn):
        insert_facts(conn, [_row("0001539838", 100.0, "MBoe")])
        fact_id = conn.execute("SELECT id FROM fact").fetchone()[0]
        record_verification(conn, fact_id, "found", page=12, printed="100")

        cell = _cell(conn, "0001539838")
        assert cell["verify_status"] == "found"
        assert cell["verified"] is True

    def test_checked_and_not_located_is_not_unchecked(self, conn):
        insert_facts(conn, [_row("0001090012", 200.0, "MBoe")])
        fact_id = conn.execute("SELECT id FROM fact").fetchone()[0]
        record_verification(conn, fact_id, "not_found")

        cell = _cell(conn, "0001090012")
        assert cell["verified"] is False
        # The distinction the colour depends on: a verdict, not a blank.
        assert cell["verify_status"] == "not_found"

    def test_an_unchecked_cell_has_no_verdict(self, conn):
        insert_facts(conn, [_row("0000717423", 300.0, "MBoe")])

        cell = _cell(conn, "0000717423")
        assert cell["verified"] is False
        assert cell["verify_status"] is None


def _cell(conn, cik, concept="proved_developed_reserves_boe"):
    rows = [r for r in panel_wide(conn)["rows"] if r["cik"] == cik]
    assert len(rows) == 1
    return rows[0]["cells"][concept]
