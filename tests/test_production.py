"""Reading the Regulation S-K Item 1204 production / price / cost table.

One table carries three panel columns, and the traps are all about units: a
price row prints a volume unit in its label, a gas price is quoted per Mcf
while the total is per BOE, and a filing repeats the whole table per segment.
"""

from __future__ import annotations

import pytest

from basin.documents.production import (
    COST,
    PRICE,
    VOLUME,
    implied_revenue,
    production_readings,
    volumes_close,
)


def _table(rows: str, *, years="2025 2024") -> str:
    """Build filing HTML from an indented text table. '|' separates cells."""
    header = "".join(f"<td>{y}</td>" for y in years.split())
    body = ""
    for line in rows.strip().splitlines():
        cells = "".join(f"<td>{c.strip()}</td>" for c in line.split("|"))
        body += f"<tr>{cells}</tr>"
    return f"<table><tr><td></td>{header}</tr>{body}</table>"


PRODUCTION_TABLE = _table("""
    Production Volumes: | |
    Crude oil (MBbls) | 11,771 | 7,048
    Natural gas (MMcf) | 22,771 | 16,308
    NGLs (MBbls) | 1,176 | 706
    Total (MBoe) | 16,742 | 10,472
    Average Sales Price (excluding commodity derivatives): | |
    Crude oil (MBbls) | 66.42 | 48.92
    Natural gas (MMcf) | 3.23 | 3.00
    Average (MBoe) | 53.24 | 39.18
    Average production cost per Boe | 12.60 | 13.56
""")


def _find(readings, concept, product=None, period="2025-12-31"):
    return next(
        (r for r in readings
         if r.concept_key == concept and r.product == product
         and r.period_end == period),
        None,
    )


class TestUnits:
    def test_a_price_row_does_not_take_the_unit_its_label_prints(self):
        # The row reads "Crude oil (MBbls)" under a price heading. $66.42 is
        # dollars per barrel, not thousands of barrels -- taking the label
        # literally turns a realized price into a volume.
        r = _find(production_readings(PRODUCTION_TABLE), PRICE, "oil")
        assert r.value == pytest.approx(66.42)
        assert r.unit == "USD/bbl"

    def test_a_volume_row_does_take_the_unit_its_label_prints(self):
        # Directly above, in the same table, the same label IS the unit.
        r = _find(production_readings(PRODUCTION_TABLE), VOLUME, "oil")
        assert r.value == pytest.approx(11_771)
        assert r.unit == "MBbls"

    def test_gas_is_priced_per_mcf_and_the_total_per_boe(self):
        readings = production_readings(PRODUCTION_TABLE)
        assert _find(readings, PRICE, "gas").unit == "USD/Mcf"
        assert _find(readings, PRICE, None).unit == "USD/Boe"

    def test_production_cost_is_per_boe_whatever_the_row_says(self):
        r = _find(production_readings(PRODUCTION_TABLE), COST)
        assert (r.value, r.unit) == (pytest.approx(12.60), "USD/Boe")


class TestSections:
    def test_a_heading_in_the_header_rows_still_governs_the_rows_below(self):
        # "Production Volumes:" is entirely non-numeric, so the table parser
        # reads it as a header row. Without seeding the metric from it the
        # whole volume section is silently dropped.
        volumes = [r for r in production_readings(PRODUCTION_TABLE)
                   if r.concept_key == VOLUME]
        assert len(volumes) == 8   # 4 rows x 2 years

    def test_a_one_line_section_does_not_govern_what_follows(self):
        # "Average production cost per Boe  12.60" carries its own figures.
        # Letting it persist labelled every later row a production cost.
        html = _table("""
            Average production cost per Boe | 12.60 | 13.56
            Some other measure | 99.00 | 98.00
        """)
        costs = [r for r in production_readings(html) if r.concept_key == COST]
        assert [r.value for r in costs] == [pytest.approx(12.60), pytest.approx(13.56)]

    def test_hedging_basis_is_read_from_the_heading(self):
        including = _table("""
            Average Sales Price (including commodity derivatives): | |
            Crude oil (MBbls) | 57.12 | 52.46
        """)
        assert _find(production_readings(including), PRICE, "oil").is_hedged is True
        assert _find(production_readings(PRODUCTION_TABLE), PRICE, "oil").is_hedged is False

    def test_hedging_is_none_when_the_filer_does_not_say(self):
        html = _table("""
            Average Sales Price: | |
            Crude oil (MBbls) | 57.12 | 52.46
        """)
        assert _find(production_readings(html), PRICE, "oil").is_hedged is None

    def test_only_a_price_carries_a_hedging_basis(self):
        # A production cost is not hedged; recording False would assert
        # something the filing does not say.
        assert _find(production_readings(PRODUCTION_TABLE), COST).is_hedged is None


class TestRefusals:
    def test_a_table_with_no_year_columns_yields_nothing(self):
        # A value without a period is not a fact.
        html = "<table><tr><td></td><td>A</td></tr>" \
               "<tr><td>Average sales price</td><td>66.42</td></tr></table>"
        assert production_readings(html) == []

    def test_a_row_that_does_not_line_up_with_the_years_is_dropped(self):
        # Guessing which figure belongs to which year is the silent error the
        # column axis exists to prevent.
        html = _table("""
            Production Volumes: | |
            Crude oil (MBbls) | 11,771
        """)
        assert [r for r in production_readings(html) if r.concept_key == VOLUME] == []

    def test_a_percentage_row_is_not_a_quantity(self):
        # "Percent of Boe from crude oil  70 %" would otherwise store 70.
        html = _table("""
            Production Volumes: | |
            Percent of Boe from crude oil | 70 | 67
        """)
        assert production_readings(html) == []


class TestVolumeIdentity:
    def test_components_summing_to_the_total_closes(self):
        # 11,771 + 1,176 + 22,771/6 = 16,742 MBoe.
        assert volumes_close(production_readings(PRODUCTION_TABLE)) == {
            "2025-12-31": True, "2024-12-31": True,
        }

    def test_a_misread_axis_fails_to_close(self):
        html = _table("""
            Production Volumes: | |
            Crude oil (MBbls) | 11,771 | 7,048
            Natural gas (MMcf) | 22,771 | 16,308
            Total (MBoe) | 99,999 | 10,472
        """)
        assert volumes_close(production_readings(html))["2025-12-31"] is False

    def test_a_period_with_no_total_is_absent_rather_than_false(self):
        # Untested is not failed: a filer printing only components is a
        # coverage question, not a parse error.
        html = _table("""
            Production Volumes: | |
            Crude oil (MBbls) | 11,771 | 7,048
        """)
        assert volumes_close(production_readings(html)) == {}


class TestRevenueReconciliation:
    def test_price_times_volume_gives_revenue_per_product(self):
        # 11,771 MBbl x $66.42 + 22,771 MMcf x $3.23 = $855.3m.
        implied = implied_revenue(production_readings(PRODUCTION_TABLE), "2025-12-31")
        assert implied == pytest.approx(11_771e3 * 66.42 + 22_771e3 * 3.23, rel=1e-6)

    def test_a_segment_table_implies_a_smaller_revenue_than_consolidated(self):
        # Both tables close the BOE identity, so only agreement with reported
        # revenue separates the company's table from one field's.
        segment = _table("""
            Production Volumes: | |
            Crude oil (MBbls) | 2,042 | 1,000
            Natural gas (MMcf) | 1,758 | 900
            Total (MBoe) | 2,335 | 1,150
            Average Sales Price: | |
            Crude oil (MBbls) | 66.42 | 48.92
            Natural gas (MMcf) | 3.23 | 3.00
        """)
        consolidated = implied_revenue(production_readings(PRODUCTION_TABLE), "2025-12-31")
        one_field = implied_revenue(production_readings(segment), "2025-12-31")
        assert one_field < consolidated / 4

    def test_nothing_is_implied_without_both_a_price_and_a_volume(self):
        html = _table("""
            Production Volumes: | |
            Crude oil (MBbls) | 11,771 | 7,048
        """)
        assert implied_revenue(production_readings(html), "2025-12-31") is None


class TestFiscalYearEnd:
    def test_a_non_calendar_year_end_is_read_from_the_header(self):
        # Evolution Petroleum closes on 30 June. Hard-coding 31 December puts
        # its figures on a period it never reported, beside calendar-year peers
        # as though the periods matched.
        html = (
            "<table><tr><td>Years Ended June 30,</td><td></td><td></td></tr>"
            "<tr><td></td><td>2025</td><td>2024</td></tr>"
            "<tr><td>Production Volumes:</td><td></td><td></td></tr>"
            "<tr><td>Crude oil (MBbls)</td><td>11,771</td><td>7,048</td></tr>"
            "</table>"
        )
        assert {r.period_end for r in production_readings(html)} == {
            "2025-06-30", "2024-06-30",
        }

    def test_december_remains_the_default(self):
        assert "2025-12-31" in {r.period_end for r in production_readings(PRODUCTION_TABLE)}
