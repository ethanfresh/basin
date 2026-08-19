"""Read the standardized measure out of the ASC 932 cash-flow note.

The standardized measure of discounted future net cash flows is the SEC's own
present value of a producer's proved reserves: future revenue at trailing
twelve-month prices, less the costs of producing and developing them, less
tax, discounted at 10%. It is the closest thing the filings hold to a valuation
that every producer computes the same way, which is what makes it comparable at
all -- and it is the only panel column whose figure the filing checks for you.

ASC 932-235-50-31 fixes the line items, so the table is the same table in every
10-K::

    Future cash inflows                                    27,159,063
    Future production costs                               (10,126,010)
    Future development costs                               (2,683,388)
    Future abandonment costs                                 (236,163)
    Future income tax expense                              (2,089,975)
    Future net cash flows                                  12,023,527
    10% annual discount for estimated timing of cash flows  (5,036,961)
    Standardized measure of discounted future net cash flows 6,986,566

Only the last line reaches the panel. The seven above it are read anyway,
because they are what makes the last one trustworthy: the deductions must sum
to future net cash flows, and future net cash flows less the discount must be
the standardized measure. Two identities over eight numbers, both stated by the
filing, both checked here. A table that fails either yields nothing.

## Scale is the whole risk

Unlike a reserve table, where the unit is the column header and the figure is
the figure as printed, this table is dollars and declares its magnitude once --
"(in thousands)" in a caption or "(Millions of dollars)" in a header cell -- and
every number beneath is silent about its own size. Reading Matador's 6,986,566
as dollars rather than thousands understates it by a factor of a thousand, and
it would look entirely reasonable.

So :func:`basin.documents.headers.declared_scale` is consulted, and a table that
declares no scale yields nothing. This is the one extractor where a missing
caption is fatal by design, because the alternative is a plausible wrong number
in the column the scale resolver uses as its own referent.

## What this is not

The *changes* in the standardized measure -- a rollforward of the same figure
with rows for revisions, extensions and production -- is a different table with
overlapping language. It is recognised and skipped: its "standardized measure,
end of year" row is the same quantity, but its other rows are flows and reading
them as levels would put a year's revisions where a present value belongs.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from basin.documents.headers import declared_scale
from basin.documents.tables import Table, parse_tables

STANDARDIZED_MEASURE = "standardized_measure"

# The line items, in the order ASC 932 lists them. `sign` is how the row enters
# the build-up: inflows add, costs and tax subtract. Filings print the
# deductions in parentheses, so the parsed value is usually already negative --
# the sign here is what the arithmetic expects, and both conventions are
# reconciled by taking absolute values before applying it.
INFLOWS = "future_cash_inflows"
PRODUCTION_COSTS = "future_production_costs"
DEVELOPMENT_COSTS = "future_development_costs"
ABANDONMENT_COSTS = "future_abandonment_costs"
INCOME_TAX = "future_income_tax"
NET_CASH_FLOWS = "future_net_cash_flows"
DISCOUNT = "discount"

_LINES: tuple[tuple[str, int, re.Pattern[str]], ...] = (
    (INFLOWS, +1, re.compile(r"(?i)^\s*future\s+cash\s+inflows?\b")),
    (PRODUCTION_COSTS, -1, re.compile(
        r"(?i)^\s*future\s+(?:cash\s+)?production\s+(?:and\s+\w+\s+)?costs?\b"
        r"|^\s*future\s+(?:lease\s+)?operating\s+(?:costs?|expenses?)\b")),
    (DEVELOPMENT_COSTS, -1, re.compile(
        r"(?i)^\s*future\s+development(?:\s+and\s+abandonment)?\s+costs?\b")),
    (ABANDONMENT_COSTS, -1, re.compile(
        r"(?i)^\s*future\s+(?:abandonment|asset\s+retirement|dismantlement|"
        r"plugging)\b")),
    (INCOME_TAX, -1, re.compile(r"(?i)^\s*future\s+income\s+tax(?:es|\s+expenses?)?\b")),
    # Tested after the deductions: "future net cash flows" must not be caught by
    # a looser pattern above, and the discount line names the same quantity.
    (NET_CASH_FLOWS, 0, re.compile(
        r"(?i)^\s*(?:undiscounted\s+)?future\s+net\s+cash\s+flows?\b"
        r"(?!\s*(?:,|\bdiscounted))")),
    (DISCOUNT, 0, re.compile(
        r"(?i)^\s*\(?\s*10\s*%|^\s*(?:annual\s+)?discount\b"
        r"|^\s*less\b.{0,24}\bdiscount\b")),
)

# The row that reaches the panel. Anchored and tested before the generic lines
# because "standardized measure of discounted future net cash flows" contains
# "future net cash flows".
# Variants seen across the cohort, all of which are the same line: a leading
# "Total" (CNX), "after tax" inserted before "discounted" (CNX), "future"
# dropped from "discounted net cash flows" (CNX again), and trailing footnote
# markers -- "(1)(2)" on Devon, "(b)" on Occidental. What must NOT match is the
# rollforward's opening balance, which names the same quantity for the prior
# year and is a different row.
_MEASURE = re.compile(
    r"(?i)^\s*(?:total\s+)?standardi[sz]ed\s+measure\b"
    r"(?:\s+of\s+(?:after[\s-]?tax\s+)?discounted(?:\s+future)?\s+net\s+"
    r"cash\s+flows?)?"
    r"(?!.*\bbeginning\b)"
)

# The rollforward of the same figure. Its rows are flows, not levels, and its
# closing row is a duplicate of the levels table -- so the whole table is
# skipped rather than partially read.
_ROLLFORWARD = re.compile(
    r"(?i)\bchanges?\s+in\s+(?:the\s+)?standardi[sz]ed\s+measure"
    r"|standardi[sz]ed\s+measure,?\s+(?:at\s+)?(?:beginning|start)\b"
    r"|\b(?:beginning|start)\s+of\s+(?:the\s+)?(?:year|period)\b"
    r"|\bat\s+january\s+1\b"
)

# The rows only a rollforward has. Title matching is not enough: Murphy's
# changes table carries no phrase naming itself, and its measure row is the
# *opening* balance -- so read as a levels table it reports every year's figure
# one year late, which is a wrong number that looks entirely plausible.
#
# These are flows. None of them can appear in the levels table, whose eight
# lines are fixed by ASC 932-235-50-31, so two of them is conclusive.
_FLOW_ROWS: tuple[re.Pattern[str], ...] = (
    re.compile(r"(?i)^\s*(?:net\s+)?changes?\s+(?:in|due\s+to)\b"),
    re.compile(r"(?i)^\s*accretion\s+of\s+discount"),
    re.compile(r"(?i)^\s*revisions?\s+of\s+previous"),
    re.compile(r"(?i)^\s*(?:sales|purchases)\s+and\s+transfers\b"),
    re.compile(r"(?i)^\s*development\s+costs\s+incurred"),
    re.compile(r"(?i)^\s*extensions,?\s+discoveries"),
    re.compile(r"(?i)^\s*net\s+change\s+in\s+income\s+tax"),
)
MIN_FLOW_ROWS = 2

_YEAR = re.compile(r"\b(?:19|20)\d{2}\b")

# A date standing alone on a row, with no figures beside it. Murphy stacks the
# note as one block per year -- "December 31, 2025" and its eight lines, then
# "December 31, 2024" and its eight -- with the regions across the columns. Read
# as a single-period table it folds three years into one, and which year's
# figures survive depends on row order, which is how a filing's FY2023 measure
# came to be stored as its FY2024.
_PERIOD_ROW = re.compile(
    r"(?i)^\s*(?:as\s+of\s+|at\s+|year\s+ended\s+)?"
    r"(?:january|february|march|april|may|june|july|august|september|october"
    r"|november|december)\s+\d{1,2},?\s+((?:19|20)\d{2})\s*[:.]?\s*$"
    r"|^\s*(?:fiscal\s+)?(?:year\s+)?((?:19|20)\d{2})\s*[:.]?\s*$"
)
_NUMBER = re.compile(r"^\(?\s*\$?\s*-?[\d,]+(?:\.\d+)?\s*\)?$")
_CURRENCY_ONLY = re.compile(r"^[\$\s%()\-—–]*$")

# Filings round each line independently, so the build-up rarely sums exactly.
# 1% absorbs that and is far tighter than the errors it catches, which are
# misread column axes and factors of a thousand.
TOLERANCE = 0.01


@dataclass(frozen=True)
class MeasureReading:
    """One period's standardized measure, with the build-up that proves it."""

    value: float
    """In dollars, scaled. Not as printed -- this is the one table where the
    printed figure is meaningless without its caption."""

    period_end: str
    scale: float
    components: dict[str, float]
    """The line items, in dollars. Kept so a reader can see the arithmetic."""

    deductions_close: bool
    discount_closes: bool
    table_index: int
    row_label: str
    source_span: str
    char_offset: int

    @property
    def checked(self) -> bool:
        return self.deductions_close and self.discount_closes


def _number(text: str) -> float | None:
    stripped = text.strip()
    if not _NUMBER.match(stripped):
        return None
    negative = stripped.startswith("(") and stripped.endswith(")")
    cleaned = stripped.strip("()$ ").replace(",", "").replace("$", "").strip()
    try:
        value = float(cleaned)
    except ValueError:
        return None
    return -value if negative else value


# Column labels that name the consolidated total of a geography-segmented
# table. ExxonMobil, Occidental and EOG lay the note out with one year and a
# column per region ("United States | Trinidad | Total"), which is the same
# disclosure with the axes exchanged.
_TOTAL_COLUMN = re.compile(
    r"(?i)^\s*(?:total|worldwide|consolidated|combined)\b"
)



# Filings that do not end in December say so in the column header: "Years Ended
# June 30,". Hard-coding 12-31 puts Evolution Petroleum's FY2025 figures on a
# December period it never reported, where they sit beside calendar-year peers
# as though the periods matched. Eight cohort filers close on a month other
# than December -- Barnwell and National Fuel Gas in September, Mexco in March,
# Evolution and Tamboran in June, Trio in October.
_MONTHS = {
    "january": "01-31", "february": "02-28", "march": "03-31", "april": "04-30",
    "may": "05-31", "june": "06-30", "july": "07-31", "august": "08-31",
    "september": "09-30", "october": "10-31", "november": "11-30",
    "december": "12-31",
}
_MONTH_DAY = re.compile(
    r"(?i)\b(january|february|march|april|may|june|july|august|september"
    r"|october|november|december)\s+(\d{1,2})\b"
)


def _month_day(header_rows) -> str:
    """The fiscal month and day the header names, defaulting to 31 December."""
    for row in header_rows:
        for cell in row:
            found = _MONTH_DAY.search(cell or "")
            if found:
                month = _MONTHS[found.group(1).lower()]
                return f"{month[:2]}-{int(found.group(2)):02d}"
    return "12-31"


def _years(table: Table) -> dict[int, str]:
    """Fiscal year end per numeric column position, from the header rows."""
    close = _month_day(table.header_rows)
    for row in reversed(table.header_rows):
        labels = [c for c in row if c and _YEAR.search(c)]
        if len(labels) > 1:
            return {
                i: f"{_YEAR.search(c).group(0)}-{close}" for i, c in enumerate(labels)
            }
    return {}


def _segmented(table: Table) -> tuple[str, int, int] | None:
    """``(period, total-column index, header label count)``.

    Returns None unless the header names exactly one year and one of its
    columns is a consolidated total. Reading a region column as the company's
    standardized measure would understate it by whatever the rest of the world
    contributes, so a table with no total column yields nothing rather than its
    largest region.

    The label count travels with the index because a header row usually carries
    one more label than a data row carries figures -- the leading cell heads the
    row-label column ("( Millions of dollars )"), which ``_rows`` has already
    stripped. Resolving that per row rather than assuming it is what keeps
    Murphy's "Total" column from being read one column to the left.
    """
    year = None
    for row in table.header_rows:
        for cell in row:
            found = _YEAR.search(cell or "")
            if found:
                if year and found.group(0) != year:
                    return None      # more than one year: not this layout
                year = found.group(0)
    if year is None:
        return None
    close = _month_day(table.header_rows)

    for row in reversed(table.header_rows):
        labels = [c for c in row if c and not _YEAR.search(c)]
        for index, label in enumerate(labels):
            if _TOTAL_COLUMN.match(label):
                return f"{year}-{close}", index, len(labels)
    return None


def _rows(table: Table) -> list[tuple[str, list[str]]]:
    grouped: dict[int, list[tuple[int, str]]] = {}
    for cell in table.cells:
        grouped.setdefault(cell.row, []).append((cell.column, cell.text))
    out = []
    for index in sorted(grouped):
        ordered = [text for _, text in sorted(grouped[index])]
        out.append((ordered[0] if ordered else "", ordered[1:]))
    return out


def _classify(label: str) -> str | None:
    if _MEASURE.match(label):
        return STANDARDIZED_MEASURE
    for key, _sign, pattern in _LINES:
        if pattern.match(label):
            return key
    return None


def is_rollforward(table: Table) -> bool:
    """Whether this is the changes table rather than the levels table."""
    texts = [c.text for c in table.cells] + [t for r in table.header_rows for t in r]
    if any(_ROLLFORWARD.search(t) for t in texts):
        return True
    flows = sum(1 for pattern in _FLOW_ROWS if any(pattern.match(t) for t in texts))
    return flows >= MIN_FLOW_ROWS


def readings_for_table(table: Table, raw: str) -> list[MeasureReading]:
    """Every period's standardized measure in one table, checked."""
    if is_rollforward(table):
        return []
    years = _years(table)
    segmented = None if years else _segmented(table)
    if not years and segmented is None:
        return []

    header_cells = [t for row in table.header_rows for t in row if t]
    scale = declared_scale(raw, table.start, header_cells=header_cells)
    if scale is None:
        # A dollar figure whose magnitude is a guess is worth less than none.
        return []

    # line -> {period: value}
    grid: dict[str, dict[str, float]] = {}
    labels: dict[str, str] = {}
    spans: dict[str, str] = {}
    current_period: str | None = None
    for label, cells in _rows(table):
        values_present = any(
            _number(c) is not None for c in cells if not _CURRENCY_ONLY.match(c)
        )
        if not values_present:
            # A bare date opens the next block of a stacked note. It governs
            # every line beneath it until the following one.
            found = _PERIOD_ROW.match(label)
            if found:
                current_period = f"{found.group(1) or found.group(2)}-12-31"
                continue

        key = _classify(label)
        if key is None:
            continue
        values = [
            v for v in (_number(c) for c in cells if not _CURRENCY_ONLY.match(c))
            if v is not None
        ]
        if segmented is not None:
            period = current_period or segmented[0]
            column, label_count = segmented[1], segmented[2]
            if label_count == len(values) + 1:
                # The header's first label heads the row-label column.
                column -= 1
            if not 0 <= column < len(values):
                continue
            grid.setdefault(key, {}).setdefault(period, values[column] * scale)
            labels.setdefault(key, label)
            spans.setdefault(key, " ".join([label] + cells)[:400])
            continue
        if len(values) != len(years):
            # The row does not line up with the year columns; guessing which
            # figure belongs to which year is the error the axis prevents.
            continue
        grid.setdefault(key, {})
        for position, value in enumerate(values):
            grid[key].setdefault(years[position], value * scale)
        labels.setdefault(key, label)
        spans.setdefault(key, " ".join([label] + cells)[:400])

    measures = grid.get(STANDARDIZED_MEASURE)
    if not measures:
        return []

    out: list[MeasureReading] = []
    for period, measure in measures.items():
        components = {
            key: values[period] for key, values in grid.items() if period in values
        }
        out.append(
            MeasureReading(
                value=measure,
                period_end=period,
                scale=scale,
                components=components,
                deductions_close=_deductions_close(components),
                discount_closes=_discount_closes(components, measure),
                table_index=table.index,
                row_label=labels[STANDARDIZED_MEASURE],
                source_span=spans[STANDARDIZED_MEASURE],
                char_offset=table.start,
            )
        )
    return out


def _deductions_close(components: dict[str, float]) -> bool:
    """inflows - production - development - abandonment - tax = net cash flows.

    Absolute values are taken before the signs are applied: some filers print
    the deductions in parentheses and some print them bare, and a reading that
    depended on which would be a formatting test rather than an arithmetic one.
    """
    net = components.get(NET_CASH_FLOWS)
    inflows = components.get(INFLOWS)
    if net is None or inflows is None or net == 0:
        return False
    deducted = sum(
        abs(components[key])
        for key in (PRODUCTION_COSTS, DEVELOPMENT_COSTS, ABANDONMENT_COSTS, INCOME_TAX)
        if key in components
    )
    return abs(abs(inflows) - deducted - abs(net)) / abs(net) <= TOLERANCE


def _discount_closes(components: dict[str, float], measure: float) -> bool:
    """future net cash flows - the 10% discount = the standardized measure."""
    net = components.get(NET_CASH_FLOWS)
    discount = components.get(DISCOUNT)
    if net is None or discount is None or measure == 0:
        return False
    return abs(abs(net) - abs(discount) - abs(measure)) / abs(measure) <= TOLERANCE


def measure_readings(raw: str) -> list[MeasureReading]:
    """Every standardized measure in a filing, from every table that states one."""
    readings: list[MeasureReading] = []
    for table in parse_tables(raw):
        texts = [c.text for c in table.cells] + [
            t for row in table.header_rows for t in row
        ]
        if not any(_MEASURE.match(t) for t in texts):
            continue
        readings.extend(readings_for_table(table, raw))
    return readings
