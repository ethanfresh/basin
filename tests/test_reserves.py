"""Reading reserve quantities off the table, for the filers XBRL cannot reach.

Every HTML shape here is copied from a real 10-K, and each one stands for a
failure the extractor had before it was written down: a combined product
column read as one of its components, a geographic table read as a product
table, a row date ignored in favour of the table's.
"""

from __future__ import annotations

from basin.documents.reserves import reserve_readings


def _table(body: str) -> str:
    return f"<html><body><table>{body}</table></body></html>"


# SM Energy, FY2025 10-K. The rollforward form: a category header row with no
# figures, and opening and closing balances beneath it.
SM_ENERGY = _table(
    """
    <tr><td colspan="3">For the Year Ended December 31, 2025</td></tr>
    <tr><td>Oil</td><td>Gas</td><td>NGLs</td><td>Total</td></tr>
    <tr><td>(MMBbl)</td><td>(Bcf)</td><td>(MMBbl)</td><td>(MMBOE)</td></tr>
    <tr><td>Total net proved reserves:</td></tr>
    <tr><td>Beginning of year</td><td>230.1</td><td>1,532.0</td>
        <td>119.5</td><td>604.9</td></tr>
    <tr><td>Production</td><td>(23.8)</td><td>(132.4)</td>
        <td>(9.7)</td><td>(55.5)</td></tr>
    <tr><td>End of year</td><td>283.9</td><td>1,598.5</td>
        <td>122.6</td><td>673.0</td></tr>
    <tr><td>Net proved developed reserves:</td></tr>
    <tr><td>Beginning of year</td><td>160.3</td><td>1,031.3</td>
        <td>71.8</td><td>404.0</td></tr>
    <tr><td>End of year</td><td>163.7</td><td>1,069.7</td>
        <td>70.3</td><td>412.3</td></tr>
    <tr><td>Net proved undeveloped reserves:</td></tr>
    <tr><td>Beginning of year</td><td>135.7</td><td>517.8</td>
        <td>52.4</td><td>274.3</td></tr>
    <tr><td>End of year</td><td>120.2</td><td>528.8</td>
        <td>52.3</td><td>260.7</td></tr>
    """
)

# EQT, FY2025 10-K. The flat form, and a column naming two products at once.
EQT = _table(
    """
    <tr><td colspan="3">December 31, 2025</td></tr>
    <tr><td></td><td>Natural Gas</td><td>NGLs and Oil</td><td>Total</td></tr>
    <tr><td>(Bcf)</td><td>(MMbbl)</td><td>(Bcfe)</td></tr>
    <tr><td>Proved developed reserves</td><td>19,237</td>
        <td>224</td><td>20,581</td></tr>
    <tr><td>Proved undeveloped reserves</td><td>7,179</td>
        <td>48</td><td>7,465</td></tr>
    <tr><td>Total proved reserves</td><td>26,416</td>
        <td>272</td><td>28,046</td></tr>
    """
)

# Barnwell, FY2024 10-K. One product per table, columns split by geography,
# and each row naming its own date.
BARNWELL = _table(
    """
    <tr><td colspan="3">Oil (Bbls)</td></tr>
    <tr><td>Canada</td><td>United States</td><td>Total</td></tr>
    <tr><td>Proved undeveloped reserves:</td></tr>
    <tr><td>Balance at September 30, 2023</td><td>92,000</td>
        <td>&#8212;</td><td>92,000</td></tr>
    <tr><td>Proved Developed Reserves, September 30, 2023</td><td>695,000</td>
        <td>112,000</td><td>807,000</td></tr>
    <tr><td>Proved Developed Reserves, September 30, 2024</td><td>783,000</td>
        <td>90,000</td><td>873,000</td></tr>
    """
)

# ExxonMobil, FY2025 10-K. Rows are regions and four separate columns are all
# in millions of barrels, so nothing identifies which is which.
EXXON = _table(
    """
    <tr><td>Proved Reserves</td><td>Crude Oil</td><td>Natural Gas Liquids</td>
        <td>Bitumen</td><td>Synthetic Oil</td></tr>
    <tr><td>(million bbls)</td><td>(million bbls)</td>
        <td>(million bbls)</td><td>(million bbls)</td></tr>
    <tr><td>Proved developed reserves</td></tr>
    <tr><td>United States</td><td>1,552</td><td>1,075</td>
        <td>&#8212;</td><td>&#8212;</td></tr>
    """
)


def _cell(readings, concept, product, period):
    return next(
        (
            r.value
            for r in readings
            if r.concept_key == concept
            and r.product == product
            and r.period_end == period
        ),
        None,
    )


class TestRollforwardForm:
    def test_reads_the_closing_balance_of_each_category(self):
        readings = reserve_readings(SM_ENERGY)
        assert _cell(readings, "proved_developed_reserves_boe", None, "2025-12-31") == 412.3
        assert _cell(readings, "proved_undeveloped_reserves_boe", None, "2025-12-31") == 260.7
        assert _cell(readings, "proved_reserves_boe", None, "2025-12-31") == 673.0

    def test_the_categories_add_up(self):
        # The gate the ingest relies on. If this stops holding, the parse is
        # wrong somewhere and the rows must not be written.
        readings = reserve_readings(SM_ENERGY)
        for product in (None, "oil", "gas", "ngl"):
            developed = _cell(readings, "proved_developed_reserves_boe", product, "2025-12-31")
            undeveloped = _cell(readings, "proved_undeveloped_reserves_boe", product, "2025-12-31")
            total = _cell(readings, "proved_reserves_boe", product, "2025-12-31")
            assert abs(developed + undeveloped - total) < 0.05

    def test_the_unit_comes_from_the_column(self):
        readings = reserve_readings(SM_ENERGY)
        units = {
            r.product: r.unit
            for r in readings
            if r.concept_key == "proved_developed_reserves_boe"
        }
        assert units == {"oil": "MMBbls", "gas": "Bcf", "ngl": "MMBbls", None: "MMBoe"}

    def test_the_opening_balance_is_the_prior_year(self):
        readings = reserve_readings(SM_ENERGY)
        assert _cell(readings, "proved_developed_reserves_boe", None, "2024-12-31") == 404.0

    def test_movement_rows_are_not_reserves(self):
        # "Production" sits between the balances and is a flow. Reading it as a
        # reserve would put a negative number in the panel.
        assert all(r.value >= 0 for r in reserve_readings(SM_ENERGY))
        assert not any(r.value == 55.5 for r in reserve_readings(SM_ENERGY))

    def test_the_period_comes_from_the_table_not_the_caller(self):
        # Three rollforwards sit side by side in one filing, each headed with
        # its own year. The fallback must not overwrite them.
        readings = reserve_readings(SM_ENERGY, fallback_period="2019-12-31")
        assert {r.period_end for r in readings} == {"2025-12-31", "2024-12-31"}


class TestFlatForm:
    def test_reads_a_category_and_its_figures_from_one_row(self):
        readings = reserve_readings(EQT)
        assert _cell(readings, "proved_developed_reserves_boe", "gas", "2025-12-31") == 19_237
        assert _cell(readings, "proved_reserves_boe", None, "2025-12-31") == 28_046

    def test_a_column_naming_two_products_is_dropped(self):
        # "NGLs and Oil" is 215.3 of NGL plus 8.6 of oil. Filed under either
        # name it is wrong by the other.
        readings = reserve_readings(EQT)
        assert not any(r.value == 224 for r in readings)
        assert _cell(readings, "proved_developed_reserves_boe", "ngl", "2025-12-31") is None


class TestSingleProductTable:
    def test_reads_the_total_column_when_the_table_names_the_product(self):
        readings = reserve_readings(BARNWELL, fallback_period="2024-09-30")
        assert _cell(readings, "proved_developed_reserves_boe", "oil", "2024-09-30") == 873_000
        assert _cell(readings, "proved_developed_reserves_boe", "oil", "2023-09-30") == 807_000

    def test_regional_columns_are_not_stored(self):
        # 783,000 is the Canadian figure; only the Total column is the
        # company's reserves.
        readings = reserve_readings(BARNWELL, fallback_period="2024-09-30")
        assert not any(r.value in (783_000, 90_000) for r in readings)


class TestRefusals:
    def test_columns_that_cannot_be_told_apart_yield_nothing(self):
        # Four columns, all "(million bbls)", rows are regions. Storing any of
        # them would be storing a guess.
        assert reserve_readings(EXXON, fallback_period="2025-12-31") == []

    def test_a_table_with_no_period_yields_nothing(self):
        no_date = SM_ENERGY.replace("For the Year Ended December 31, 2025", "")
        assert reserve_readings(no_date) == []

    def test_prose_mentioning_both_categories_is_not_a_table(self):
        prose = (
            "<p>We expect to convert proved undeveloped reserves to proved "
            "developed reserves within five years. 412.3</p>"
        )
        assert reserve_readings(prose, fallback_period="2025-12-31") == []


# Continental Resources, FY2024 10-K. Proved developed is never printed as a
# line: only its producing and non-producing halves are.
CONTINENTAL = _table(
    """
    <tr><td>Crude Oil (MBbls)</td><td>Natural Gas (MMcf)</td>
        <td>Total (MBoe)</td></tr>
    <tr><td>Proved developed producing</td><td>405,269</td>
        <td>3,288,444</td><td>953,343</td></tr>
    <tr><td>Proved developed non-producing</td><td>3,125</td>
        <td>19,901</td><td>6,442</td></tr>
    <tr><td>Proved undeveloped</td><td>430,512</td>
        <td>2,607,079</td><td>865,025</td></tr>
    <tr><td>Total proved reserves</td><td>838,906</td>
        <td>5,915,424</td><td>1,824,810</td></tr>
    """
)


class TestDevelopedComponents:
    def test_producing_and_non_producing_are_summed(self):
        readings = reserve_readings(CONTINENTAL, fallback_period="2024-12-31")
        developed = _cell(
            readings, "proved_developed_reserves_boe", None, "2024-12-31"
        )
        assert developed == 953_343 + 6_442

    def test_the_sum_closes_against_the_printed_total(self):
        # The check that makes the derivation safe to store at all.
        readings = reserve_readings(CONTINENTAL, fallback_period="2024-12-31")
        developed = _cell(readings, "proved_developed_reserves_boe", None, "2024-12-31")
        undeveloped = _cell(readings, "proved_undeveloped_reserves_boe", None, "2024-12-31")
        total = _cell(readings, "proved_reserves_boe", None, "2024-12-31")
        assert developed + undeveloped == total

    def test_producing_alone_is_never_stored_as_developed(self):
        # 953,343 is PDP. Filed as proved developed it understates by 6,442.
        readings = reserve_readings(CONTINENTAL, fallback_period="2024-12-31")
        assert not any(
            r.value == 953_343 and r.concept_key == "proved_developed_reserves_boe"
            for r in readings
        )
