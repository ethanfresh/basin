"""Reading the standardized measure out of the ASC 932 cash-flow note.

ASC 932-235-50-31 fixes the eight line items, so the levels table is unusually
uniform. What is not uniform is everything around it: the scale lives in a
caption, the majors put regions across the columns instead of years, and the
rollforward of the same figure is a different table whose opening balance is
the previous year's answer.
"""

from __future__ import annotations

import pytest

from basin.documents.cashflow import measure_readings

LEVELS = """
<p>relating to proved reserves for the years ended December 31 (in thousands).</p>
<table>
  <tr><td></td><td>2025</td><td>2024</td></tr>
  <tr><td>Future cash inflows</td><td>27,159,063</td><td>29,033,021</td></tr>
  <tr><td>Future production costs</td><td>(10,126,010)</td><td>(10,126,454)</td></tr>
  <tr><td>Future development costs</td><td>(2,683,388)</td><td>(2,705,340)</td></tr>
  <tr><td>Future abandonment costs</td><td>(236,163)</td><td>(158,346)</td></tr>
  <tr><td>Future income tax expense</td><td>(2,089,975)</td><td>(3,326,489)</td></tr>
  <tr><td>Future net cash flows</td><td>12,023,527</td><td>12,716,392</td></tr>
  <tr><td>10% annual discount for estimated timing of cash flows</td>
      <td>(5,036,961)</td><td>(5,339,842)</td></tr>
  <tr><td>Standardized measure of discounted future net cash flows</td>
      <td>6,986,566</td><td>7,376,550</td></tr>
</table>
"""


def _at(readings, period):
    return next((r for r in readings if r.period_end == period), None)


class TestLevels:
    def test_the_measure_is_read_and_scaled(self):
        r = _at(measure_readings(LEVELS), "2025-12-31")
        assert r.value == pytest.approx(6_986_566_000)
        assert r.scale == 1000

    def test_both_identities_are_checked(self):
        # inflows - costs - tax = net cash flows; net - discount = measure.
        r = _at(measure_readings(LEVELS), "2025-12-31")
        assert r.deductions_close and r.discount_closes and r.checked

    def test_every_column_becomes_a_period(self):
        assert {r.period_end for r in measure_readings(LEVELS)} == {
            "2025-12-31", "2024-12-31",
        }

    def test_a_broken_buildup_is_read_but_not_checked(self):
        # The figure is still the filer's, and still citable. It just carries
        # no arithmetic behind it, which is what `checked` says.
        broken = LEVELS.replace("12,023,527", "99,999,999")
        r = _at(measure_readings(broken), "2025-12-31")
        assert r.value == pytest.approx(6_986_566_000)
        assert not r.checked


class TestScale:
    def test_a_table_declaring_no_scale_yields_nothing(self):
        # Dollars whose magnitude is a guess are worth less than no dollars:
        # reading 6,986,566 as dollars understates it a thousandfold and looks
        # entirely reasonable.
        assert measure_readings(LEVELS.replace("(in thousands)", "")) == []

    def test_a_header_cell_declares_the_scale_too(self):
        html = LEVELS.replace(
            "relating to proved reserves for the years ended December 31 (in thousands).",
            "relating to proved reserves.",
        ).replace("<tr><td></td><td>2025</td>", "<tr><td>(Millions of dollars)</td><td>2025</td>")
        assert _at(measure_readings(html), "2025-12-31").value == pytest.approx(
            6_986_566_000_000
        )


class TestRollforward:
    def test_the_changes_table_is_skipped_even_when_it_names_itself(self):
        html = """
        <p>Changes in the standardized measure (in thousands).</p>
        <table>
          <tr><td></td><td>2025</td></tr>
          <tr><td>Standardized measure, beginning of year</td><td>5,395,900</td></tr>
          <tr><td>Standardized measure, end of year</td><td>4,624,200</td></tr>
        </table>
        """
        assert measure_readings(html) == []

    def test_a_rollforward_that_does_not_name_itself_is_caught_by_its_rows(self):
        # Murphy's changes table carries no phrase naming it, and its measure
        # row is the OPENING balance -- so read as levels it reports every year
        # one year late, which is a wrong number that looks entirely plausible.
        html = """
        <p>(Millions of dollars)</p>
        <table>
          <tr><td></td><td>2025</td></tr>
          <tr><td>Net changes in prices and production costs</td><td>(1,126.6)</td></tr>
          <tr><td>Accretion of discount</td><td>598.3</td></tr>
          <tr><td>Revisions of previous quantity estimates</td><td>403.9</td></tr>
          <tr><td>Standardized measure</td><td>5,395.9</td></tr>
        </table>
        """
        assert measure_readings(html) == []

    def test_one_flow_row_alone_does_not_condemn_a_table(self):
        # The threshold is two: a levels table can legitimately carry a line
        # whose wording brushes a flow, and dropping it on one match would
        # trade a wrong number for a missing one.
        html = LEVELS.replace(
            "<tr><td>Future abandonment costs</td><td>(236,163)</td><td>(158,346)</td></tr>",
            "<tr><td>Net changes in the estimate</td><td>(236,163)</td><td>(158,346)</td></tr>",
        )
        assert measure_readings(html) != []


class TestSegmentedLayout:
    # ExxonMobil, Occidental, EOG and Murphy lay the note out with one block per
    # year and a column per region, which is the same disclosure transposed.
    SEGMENTED = """
    <p>(Millions of dollars)</p>
    <table>
      <tr><td>(Millions of dollars)</td><td>United States</td><td>Canada</td><td>Total</td></tr>
      <tr><td>December 31, 2025</td><td></td><td></td><td></td></tr>
      <tr><td>Future cash inflows</td><td>15,502.7</td><td>6,850.9</td><td>22,353.6</td></tr>
      <tr><td>Future production costs</td><td>(6,707.7)</td><td>(4,240.9)</td><td>(10,948.6)</td></tr>
      <tr><td>Future development costs</td><td>(1,969.6)</td><td>(812.6)</td><td>(2,782.2)</td></tr>
      <tr><td>Future income taxes</td><td>(602.4)</td><td>(342.8)</td><td>(945.2)</td></tr>
      <tr><td>Future net cash flows</td><td>6,223.0</td><td>1,454.6</td><td>7,677.6</td></tr>
      <tr><td>10% annual discount</td><td>(2,459.4)</td><td>(625.4)</td><td>(3,084.8)</td></tr>
      <tr><td>Standardized measure</td><td>3,763.6</td><td>829.2</td><td>4,592.8</td></tr>
      <tr><td>December 31, 2024</td><td></td><td></td><td></td></tr>
      <tr><td>Future cash inflows</td><td>18,118.1</td><td>6,304.4</td><td>24,422.5</td></tr>
      <tr><td>Standardized measure</td><td>4,000.0</td><td>1,395.9</td><td>5,395.9</td></tr>
    </table>
    """

    def test_the_total_column_is_taken_not_a_region(self):
        # Storing the United States column as the company's measure would
        # understate it by whatever the rest of the world contributes.
        r = _at(measure_readings(self.SEGMENTED), "2025-12-31")
        assert r.value == pytest.approx(4_592.8e6)

    def test_a_stacked_block_opens_a_new_period(self):
        # A bare date row governs the lines beneath it. Folding both blocks into
        # one period stores whichever year happens to come first.
        r = _at(measure_readings(self.SEGMENTED), "2024-12-31")
        assert r.value == pytest.approx(5_395.9e6)

    def test_the_row_label_column_is_not_counted_as_data(self):
        # The header carries one more label than a data row carries figures,
        # because its first cell heads the row-label column. Off by one here
        # reads Canada as the total.
        assert _at(measure_readings(self.SEGMENTED), "2025-12-31").value != pytest.approx(
            829.2e6
        )

    def test_a_segmented_table_with_no_total_column_yields_nothing(self):
        html = self.SEGMENTED.replace("<td>Total</td>", "<td>International</td>")
        assert measure_readings(html) == []


class TestFiscalYearEnd:
    def test_a_september_close_is_read_from_the_header(self):
        # Barnwell and National Fuel Gas close on 30 September.
        html = LEVELS.replace(
            "<tr><td></td><td>2025</td><td>2024</td></tr>",
            "<tr><td>Years Ended September 30,</td><td></td><td></td></tr>"
            "<tr><td></td><td>2025</td><td>2024</td></tr>",
        )
        assert {r.period_end for r in measure_readings(html)} == {
            "2025-09-30", "2024-09-30",
        }

    def test_december_remains_the_default(self):
        assert _at(measure_readings(LEVELS), "2025-12-31") is not None
