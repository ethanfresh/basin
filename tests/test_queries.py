"""Read queries — especially the ones that must not imply false comparability."""

from __future__ import annotations

import pytest

from basin.facts.xbrl import FactRow
from basin.store import connect, insert_facts, record_filing, upsert_company
from basin.store.queries import filing_url, panel, panel_wide, summary, unit_groups


@pytest.fixture
def conn(tmp_path):
    connection = connect(tmp_path / "q.db")
    for cik, name, ticker in [
        ("0001539838", "Diamondback Energy, Inc.", "FANG"),
        ("0001090012", "Devon Energy Corp", "DVN"),
        ("0000717423", "Murphy Oil Corp", "MUR"),
    ]:
        # Cohort membership is what puts a company in the panel at all, so the
        # fixture has to grant it: a company with no cohort is out of scope,
        # not a member with empty cells.
        upsert_company(connection, cik, name, ticker=ticker, cohort="Oil & Gas E&P")
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


class TestPanelMembership:
    """The panel shows the cohort, not everything the store happens to hold."""

    @pytest.fixture
    def mixed(self, conn):
        # One member, one company that has left the cohort, one never admitted.
        # Cleared with SQL rather than upsert_company, which COALESCEs and so
        # cannot unset a cohort -- that is deliberate there, and inconvenient
        # exactly once, here.
        with conn:
            conn.execute(
                "UPDATE company SET cohort = NULL, "
                "notes = 'dropped: not in the current EDGAR pull' "
                "WHERE cik = '0001090012'"
            )
            conn.execute("UPDATE company SET cohort = NULL WHERE cik = '0000717423'")
        insert_facts(conn, [
            _row("0001539838", 2_521_028.0, "MBoe"),
            _row("0001090012", 1_000.0, "MBoe"),
            _row("0000717423", 2_000.0, "MBoe"),
        ])
        return conn

    def test_only_cohort_members_appear(self, mixed):
        rows = panel_wide(mixed)["rows"]
        assert [r["ticker"] for r in rows] == ["FANG"]

    def test_a_dropped_company_is_excluded_not_merely_blank(self, mixed):
        # Its facts are still in the store -- membership is cleared, never
        # deleted -- so the panel has to exclude the row rather than rely on
        # the cells being empty. They are not empty.
        assert "DVN" not in {r["ticker"] for r in panel_wide(mixed)["rows"]}
        everything = panel_wide(mixed, include_uncohorted=True)["rows"]
        assert {r["ticker"] for r in everything} == {"FANG", "DVN", "MUR"}

    def test_the_escape_hatch_is_for_coverage_not_for_the_panel(self, mixed):
        # Coverage reporting asks what the store holds; the panel asks what is
        # comparable. Both need answering, from one query.
        assert len(panel_wide(mixed, include_uncohorted=True)["rows"]) > len(
            panel_wide(mixed)["rows"]
        )

    def test_a_named_cohort_still_narrows_within_membership(self, mixed):
        upsert_company(mixed, "0000717423", "Murphy Oil Corp",
                       cohort="Oil & Gas Integrated")
        assert [r["ticker"] for r in panel_wide(mixed, cohort="Oil & Gas E&P")["rows"]] == ["FANG"]
        assert [r["ticker"] for r in panel_wide(mixed, cohort="Oil & Gas Integrated")["rows"]] == ["MUR"]
