"""Read production, realized price and production cost out of the S-K 1204 table.

Regulation S-K Item 1204 requires every producer to disclose, for each of the
last three fiscal years and by product: production volume, average sales price
per unit, and average production cost per unit. Every filer in the cohort files
it. Almost none tag it — XBRL reaches 41% of the cohort for production volume,
**9% for realized price and 2% for production cost**, which is three panel
columns effectively empty against a disclosure that is mandatory.

The table is one table and it carries all three, so one parse fills all three
columns. :mod:`basin.documents.sites` locates it; this reads it.

## The shape

Filers lay it out as sections stacked in one table, products down and fiscal
years across::

    Production Volumes:                          2018      2017      2016
      Crude oil (MBbls)                        11,771     7,048     5,126
      Natural gas (MMcf)                       22,771    16,308    19,001
      Total (MBoe)                             16,742    10,472     8,896
    Average Sales Price (including commodity derivatives):
      Crude oil (MBbls)                     $   57.12  $  52.46  $  68.46
      Natural gas (MMcf)                    $    3.16  $   2.93  $   3.24
    Average Direct LOE and Workover per Boe   $   12.60  $  13.56  $  16.77

A label-only row sets the metric for the rows beneath it; a row with figures
names a product and carries one value per year. So the parse is a small state
machine over rows, not a header lookup.

## The trap this module exists to avoid

**A price row's unit is not the unit its label prints.** Under an "Average Sales
Price" heading, the row still reads ``Crude oil (MBbls)`` — but $57.12 is not
11,771 thousand barrels of anything, it is dollars per barrel. Taking the
parenthesised unit literally, which is the correct thing to do for the volume
section directly above, turns a realized price into a volume in the same table.
The unit of a price is therefore derived from the *product and the section*,
never read off the row.

The same holds for cost. "Average production cost, per BOE" is USD/Boe whatever
its row label says about barrels.

## What is refused

A section whose metric cannot be identified, a row whose product cannot be
identified, and a column whose fiscal year cannot be read all yield nothing. A
figure whose meaning depends on a guess is the failure the store exists to
prevent, and a missing cell shows as a coverage gap, which is honest.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from basin.documents.tables import Table, company_column, parse_tables

# The metric a section heading announces, and whether the figures beneath it
# are hedged. Order matters: "average production cost" must be tested before
# the bare price pattern, because a heading can carry both words.
PRICE = "average_sales_price"
COST = "production_cost_per_boe"
VOLUME = "production_volume"

_SECTIONS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        COST,
        re.compile(
            r"(?i)average\s+production\s+costs?"
            r"|production\s+costs?\s+per\b"
            r"|(?:direct\s+)?(?:lease\s+operating\s+expense|LOE)[^.]{0,40}\bper\b"
            # The unit-cost table: a heading that names the denominator once
            # and lists the cost components beneath it. This is how most of the
            # cohort discloses it -- "Average cost per Boe:", "Costs and
            # Expenses (per Boe):", "Operating Expenses (per BOE):" -- and
            # matching only the S-K 1204 phrasing reached 36 filers of 91.
            r"|(?:average\s+)?(?:unit\s+)?costs?\s+(?:and\s+expenses\s+)?"
            r"(?:\(?\s*(?:in\s+)?\$?\s*)?per\s+\w*(?:boe|mcfe?|bbl|barrel)"
            r"|(?:operating|production)\s+expenses?\s*\(?\s*\$?\s*per\s+"
            r"\w*(?:boe|mcfe?|bbl|barrel)"
        ),
    ),
    (PRICE, re.compile(r"(?i)average\s+(?:realized\s+|net\s+)?sales?\s+price"
                       r"|average\s+realized\s+price"
                       # ExxonMobil heads it "Average production prices", which
                       # is a price despite the word production; the cost
                       # pattern above has already claimed "production costs",
                       # so the two cannot be confused.
                       r"|average\s+production\s+prices?")),
    (VOLUME, re.compile(r"(?i)^\s*(?:net\s+|total\s+|annual\s+)*production"
                        r"(?:\s+volumes?|\s+data)?\s*[:.]?\s*$"
                        r"|^\s*production\s+volumes?\b")),
)

# Hedging changes what a realized price means, and filers print both. A price
# that silently mixes the two across companies is a comparison that does not
# hold, so the basis travels with the value.
_HEDGED = re.compile(
    r"(?i)\bincluding\b[^)]{0,40}(?:derivativ|hedg)|after\s+(?:the\s+)?(?:effect\s+of\s+)?"
    r"(?:commodity\s+)?(?:derivativ|hedg)|\bwith\s+hedg"
)
_UNHEDGED = re.compile(
    r"(?i)\bexcluding\b[^)]{0,40}(?:derivativ|hedg)|before\s+(?:the\s+)?(?:effect\s+of\s+)?"
    r"(?:commodity\s+)?(?:derivativ|hedg)|without\s+hedg|\bunhedged\b"
)

# Products, as filers label the rows. Tested in this order: "natural gas
# liquids" contains "natural gas", and "oil equivalent" contains "oil".
_TOTAL = re.compile(r"(?i)\b(?:total|combined|equivalent|boe|mboe|mmboe|average)\b")
_PRODUCTS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ngl", re.compile(r"(?i)\bngls?\b|natural\s+gas\s+liquids?")),
    ("gas", re.compile(r"(?i)\b(?:natural\s+|residue\s+)?gas\b|\bmcf|\bbcf")),
    ("oil", re.compile(r"(?i)\b(?:crude\s+)?oil\b|\bcondensate\b|\bbbls?\b|\bbarrels?\b")),
)

# The unit a price carries, by product. Realized prices are quoted per barrel
# for liquids and per Mcf for gas, by universal convention and by the way the
# filings print them; the row label's own unit describes the volume section
# above, not this one.
_PRICE_UNIT = {
    "oil": "USD/bbl",
    "ngl": "USD/bbl",
    "gas": "USD/Mcf",
    None: "USD/Boe",
}
# The denominator a per-unit cost is quoted against, read from the row or its
# section heading. Hard-coding USD/Boe was wrong for a real and common case:
# gas-weighted filers quote "Average Production Costs ($/Mcfe)", and $0.06 per
# Mcfe stored as $0.06 per BOE is off by six and lands two orders of magnitude
# below every peer -- a number no reader would trust and none should.
_COST_UNITS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bmcfe\b|per\s+thousand\s+cubic\s+feet\s+equivalent"), "USD/Mcfe"),
    (re.compile(r"(?i)\bmcf\b|per\s+thousand\s+cubic\s+feet"), "USD/Mcf"),
    (re.compile(r"(?i)\bboe\b|barrels?\s+of\s+oil\s+equivalent"
                r"|oil[\s-]equivalent\s+barrels?|equivalent\s+barrels?"), "USD/Boe"),
    # A bare "per barrel" on a cost row means per barrel of oil equivalent.
    # Chevron heads it "Average production costs, per barrel" and ExxonMobil
    # "per oil-equivalent barrel"; nobody discloses the cost of lifting a
    # barrel of crude in isolation, because the expense is not separable by
    # product. Reading it as USD/bbl splits one column into two unit groups
    # that mean the same thing.
    (re.compile(r"(?i)per\s+(?:net\s+)?(?:bbl|barrels?)\b"), "USD/Boe"),
)

# Denominators that are not a quantity of hydrocarbon. "Production cost per
# sales dollar" is a margin ratio and belongs in no per-unit cost column;
# stored as USD/Boe it reads as a producer lifting barrels for four cents.
_NOT_A_QUANTITY = re.compile(
    r"(?i)per\s+(?:sales\s+)?dollar|per\s+share|per\s+unit\b(?!\s+of\s+production)"
    r"|\bpercent|%|per\s+ton\b|per\s+acre\b|per\s+well\b"
)

# The volume unit a price's denominator refers to. "USD/Mcf" x a volume in Mcf
# is revenue; x a volume in BOE is nothing.
_PRICE_VOLUME = {"USD/bbl": "bbl", "USD/Mcf": "Mcf", "USD/Boe": "Boe"}

# Volume units, read from the row label because there the label is telling the
# truth. Same vocabulary as basin.facts.units.
_VOLUME_UNITS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)\bmmboe\b"), "MMBoe"),
    (re.compile(r"(?i)\bmboe\b"), "MBoe"),
    (re.compile(r"(?i)\bboe\b"), "Boe"),
    (re.compile(r"(?i)\bmmbbls?\b"), "MMBbls"),
    (re.compile(r"(?i)\bmbbls?\b"), "MBbls"),
    (re.compile(r"(?i)\bbbls?\b|\bbarrels?\b"), "bbl"),
    (re.compile(r"(?i)\bbcfe\b"), "Bcfe"),
    (re.compile(r"(?i)\bbcf\b"), "Bcf"),
    (re.compile(r"(?i)\bmmcfe\b"), "MMcfe"),
    (re.compile(r"(?i)\bmmcf\b"), "MMcf"),
    (re.compile(r"(?i)\bmcfe\b"), "Mcfe"),
    (re.compile(r"(?i)\bmcf\b"), "Mcf"),
)

_YEAR = re.compile(r"\b(19|20)\d{2}\b")

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

_NUMBER = re.compile(r"^\(?\$?\s*-?[\d,]+(?:\.\d+)?\s*\)?$")
_CURRENCY_ONLY = re.compile(r"^[\$\s%()]*$")

# A row of percentages is a mix ratio ("Percent of Boe from crude oil"), not a
# quantity. Reading it as one puts 70 in a production column.
_PERCENT = re.compile(r"(?i)percent|%\s*$|\bmix\b")

# Inside a unit-cost section the rows are the cost components, and only one of
# them is the lifting cost the panel column means. Taking every row would put
# depletion and corporate overhead in a production-cost column, where they
# would sit beside real lifting costs at two to three times the value.
_LIFTING_COST = re.compile(
    r"(?i)^\s*(?:average\s+|total\s+|direct\s+|net\s+|cash\s+)*"
    r"(?:lease\s+operating(?:\s+expenses?|\s+costs?)?"
    r"|\bLOE\b|lifting\s+costs?"
    r"|production\s+(?:costs?|expenses?)"
    r"|operating\s+(?:costs?|expenses?))\b"
)

# Components that are not lifting cost, tested first because several of them
# contain words the pattern above accepts -- "Depletion expense", "General and
# administrative expense", "Total operating expenses".
_NOT_LIFTING = re.compile(
    r"(?i)deprecia|deplet|amorti|\bDD&A\b|general\s+and\s+admin|\bG&A\b"
    r"|interest|explorat|impair|accretion|income\s+tax|share[\s-]based"
    r"|gathering|transport|processing|marketing|midstream"
    r"|severance|ad\s+valorem|production\s+tax|\btaxes\b"
    r"|^\s*total\b|revenue|\bnetback\b|realized|\bprice\b"
)


@dataclass(frozen=True)
class ProductionReading:
    """One S-K 1204 figure, with the coordinates that make it checkable."""

    concept_key: str
    product: str | None
    """oil / gas / ngl, or None for the equivalent (total) row."""

    value: float
    unit: str
    period_end: str
    is_hedged: bool | None
    """Only meaningful for a realized price. None when the filer does not say."""

    table_index: int
    section_label: str
    row_label: str
    column_label: str
    source_span: str
    char_offset: int


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


def _product(label: str) -> str | None:
    """The product a row names, or None for a total/equivalent row."""
    for name, pattern in _PRODUCTS:
        if pattern.search(label):
            # "Total (MBoe)" contains no product word; "Average (MBoe)" is the
            # equivalent row of a price section and is likewise the total.
            if name == "oil" and _TOTAL.search(label) and not re.search(
                r"(?i)\b(?:crude\s+)?oil\b", label
            ):
                return None
            return name
    return None


def _cost_unit(label: str, section_label: str) -> str | None:
    """What a per-unit cost is quoted per, from the row or its heading.

    The row is read first and the heading second: a unit-cost table states the
    denominator once in its heading ("Average cost per Boe:") and its rows name
    only the cost, while an S-K 1204 table states it on the row itself
    ("Average production cost, per BOE"). Returns None when neither says, and
    when what they say is not a quantity of hydrocarbon.
    """
    for text in (label, section_label):
        if not text:
            continue
        if _NOT_A_QUANTITY.search(text):
            return None
        for pattern, unit in _COST_UNITS:
            if pattern.search(text):
                return unit
    return None


def _volume_unit(label: str) -> str | None:
    for pattern, canonical in _VOLUME_UNITS:
        if pattern.search(label):
            return canonical
    return None


def _section(label: str) -> tuple[str, bool | None] | None:
    """The metric a heading announces, and its hedging basis."""
    for metric, pattern in _SECTIONS:
        if pattern.search(label):
            hedged: bool | None = None
            if _HEDGED.search(label):
                hedged = True
            elif _UNHEDGED.search(label):
                hedged = False
            return metric, hedged
    return None


def _years(table: Table) -> dict[int, str]:
    """Fiscal year end per numeric column position, from the header rows.

    Positional, not by raw column index: header rows omit the leading label
    cell that data rows carry, so the *sequence* of year labels is what lines
    up with the sequence of figures in a row. A table whose header names no
    year yields nothing, because a value without a period is not a fact.
    """
    close = _month_day(table.header_rows)
    for row in reversed(table.header_rows):
        years = [_YEAR.search(cell) for cell in row if cell]
        found = [m for m in years if m]
        if len(found) >= 1 and len(found) == len([c for c in row if c and _YEAR.search(c)]):
            labels = [c for c in row if c and _YEAR.search(c)]
            return {
                i: f"{_YEAR.search(label).group(0)}-{close}"
                for i, label in enumerate(labels)
            }
    return {}


def _segmented(table: Table) -> tuple[str, int, int] | None:
    """``(period, company-column index, header label count)``, or None.

    The majors transpose this disclosure: a column per region and one for the
    company, with the year stated once. ExxonMobil heads it "(dollars per unit)
    | United States | Canada/Other Americas | ... | Total | 2025", which has no
    year *columns* at all, so the ordinary axis finds nothing and the whole
    table is skipped.

    Only tables naming exactly one year qualify. Two years and regions at once
    is a layout this cannot read, and guessing which figure belongs to which
    pairing is the error the axis exists to prevent.

    The label count travels with the index because a header row usually carries
    one more label than a data row carries figures -- its first cell heads the
    row-label column -- and resolving that per row rather than assuming it is
    what keeps the column before Total from being read as the company.
    """
    years = {
        found.group(0)
        for row in table.header_rows
        for cell in row
        if cell and (found := _YEAR.search(cell))
    }
    if len(years) != 1:
        return None
    close = _month_day(table.header_rows)
    period = f"{years.pop()}-{close}"

    for row in reversed(table.header_rows):
        labels = [c for c in row if c and not _YEAR.search(c)]
        if len(labels) < 2:
            continue
        chosen = company_column(labels)
        if chosen is not None:
            return period, chosen, len(labels)
    return None


def _rows(table: Table) -> list[tuple[int, str, list[str]]]:
    """``(row index, label, ordered cell texts)`` for every row."""
    grouped: dict[int, list[tuple[int, str]]] = {}
    for cell in table.cells:
        grouped.setdefault(cell.row, []).append((cell.column, cell.text))
    out: list[tuple[int, str, list[str]]] = []
    for index in sorted(grouped):
        ordered = [text for _, text in sorted(grouped[index])]
        label = ordered[0] if ordered else ""
        out.append((index, label, ordered[1:]))
    return out


def readings_for_table(table: Table, raw: str) -> list[ProductionReading]:
    """Every S-K 1204 figure in one table."""
    years = _years(table)
    # Both axes are resolved, and the row decides which applies. A header
    # naming one year is ambiguous on its own: it is a single-period table when
    # its rows carry one figure, and a region-segmented one when they carry
    # eight. ExxonMobil's is the second, and treating the year map as
    # authoritative dropped every row of it.
    segmented = _segmented(table)
    if not years and segmented is None:
        return []

    readings: list[ProductionReading] = []
    metric: str | None = None
    hedged: bool | None = None
    section_label = ""

    # The first section heading is often the last header row rather than a data
    # row: the parser treats "Production Volumes:" as a header because it is
    # entirely non-numeric, and it stands above the year columns. Seeding from
    # it is what makes the volume section readable at all — without this the
    # rows beneath it have no metric and the whole section is dropped, which is
    # how production volume stayed the emptiest of the three columns.
    for row in reversed(table.header_rows):
        labels = [c for c in row if c]
        if len(labels) == 1:
            heading = _section(labels[0])
            if heading:
                metric, hedged = heading
                section_label = labels[0]
        if labels:
            break

    for _index, label, cells in _rows(table):
        values = [v for v in (_number(c) for c in cells if not _CURRENCY_ONLY.match(c))
                  if v is not None]

        heading = _section(label)
        if heading and not values:
            # A label-only row announces the metric for what follows.
            metric, hedged = heading
            section_label = label
            continue
        one_line = bool(heading and values)
        if one_line:
            # A one-line section: "Average production cost, per BOE  $ 12.60".
            # It governs its own row and nothing after it — a stacked section
            # heading applies downwards, a heading with its own figures does
            # not, and treating them alike labelled every subsequent row of the
            # table as a production cost.
            metric, hedged = heading
            section_label = label
        if metric is None or not values:
            continue
        if _PERCENT.search(label) or any("%" in c for c in cells):
            continue
        if metric == COST and not one_line:
            # A row inside a cost section. Only the lifting-cost line is this
            # column; the rest of the section is depletion, overhead and taxes.
            if _NOT_LIFTING.search(label) or not _LIFTING_COST.match(label):
                continue
        row_years = years
        if len(values) != len(years):
            if segmented is None:
                # The row lines up with neither axis. Guessing which figure
                # belongs to which period is the silent error the axis exists
                # to prevent.
                continue
            period, column, label_count = segmented
            if label_count == len(values) + 1:
                # The header's first label heads the row-label column.
                column -= 1
            if not 0 <= column < len(values):
                continue
            values, row_years = [values[column]], {0: period}

        product = _product(label)
        if metric == COST:
            # A per-unit cost is a company-level rate, and its label names a
            # denominator rather than a product: "Average Production Costs
            # ($/Mcfe)" is not a gas cost, but "Mcfe" contains "Mcf" and was
            # read as one. Where a filer really does report cost by product --
            # Suncor splits bitumen from synthetic crude -- the row label is
            # kept as the basis rather than made part of the cell's identity.
            product = None
        if metric == PRICE:
            unit = _PRICE_UNIT[product]
        elif metric == COST:
            unit = _cost_unit(label, section_label)
            if unit is None:
                # Neither the row nor its heading says what the cost is per.
                # A per-unit cost whose denominator is a guess cannot be
                # compared with anything, which is the only thing it is for.
                continue
        else:
            unit = _volume_unit(label) or ""
            if not unit:
                continue

        for position, value in enumerate(values):
            readings.append(
                ProductionReading(
                    concept_key=metric,
                    product=product,
                    value=value,
                    unit=unit,
                    period_end=row_years[position],
                    is_hedged=hedged if metric == PRICE else None,
                    table_index=table.index,
                    section_label=section_label,
                    row_label=label,
                    column_label=str(row_years[position][:4]),
                    source_span=" ".join([label] + cells)[:400],
                    char_offset=table.start,
                )
            )
        if one_line:
            metric, hedged, section_label = None, None, ""
    return readings


def _mentions_a_section(table: Table) -> bool:
    """Whether any cell OR header of *table* announces one of the metrics.

    Headers are searched as well as cells, and that is load-bearing rather than
    defensive: "Production Volumes:" is entirely non-numeric, so the table
    parser classifies it as a header row and it never appears in ``cells``.
    Testing cells alone skipped every table whose only section heading sat at
    the top -- which is the ordinary layout, and which is the volume section in
    particular.
    """
    texts = [cell.text for cell in table.cells]
    texts.extend(text for row in table.header_rows for text in row)
    return any(pattern.search(text) for text in texts for _, pattern in _SECTIONS)


def production_readings(raw: str) -> list[ProductionReading]:
    """Every S-K 1204 figure in a filing, from every table that carries one."""
    readings: list[ProductionReading] = []
    for table in parse_tables(raw):
        if not _mentions_a_section(table):
            continue
        readings.extend(readings_for_table(table, raw))
    return readings


# Gas converts to oil equivalent at 6 Mcf per barrel, by Regulation S-K 1200
# definition. Repeated here rather than imported so this module stays a pure
# document reader; basin.facts.units holds the same constant for the store.
MCF_PER_BOE = 6.0

_TO_BOE: dict[str, float] = {
    "MMBoe": 1e6, "MBoe": 1e3, "Boe": 1.0, "boe": 1.0,
    "MMBbls": 1e6, "MBbls": 1e3, "bbl": 1.0, "Bbls": 1.0,
    "Bcf": 1e9 / (MCF_PER_BOE * 1e3), "MMcf": 1e6 / (MCF_PER_BOE * 1e3),
    "Mcf": 1e3 / (MCF_PER_BOE * 1e3),
    "Bcfe": 1e9 / (MCF_PER_BOE * 1e3), "MMcfe": 1e6 / (MCF_PER_BOE * 1e3),
    "Mcfe": 1e3 / (MCF_PER_BOE * 1e3),
}

# Filers print volumes to the unit, so the components rarely sum exactly to the
# printed total. 1.5% absorbs that and is far tighter than the errors this
# catches, which are misread axes and factors of six.
VOLUME_TOLERANCE = 0.015

# Revenue reconciliation is looser by nature: realized price is an average over
# the year, revenue includes items the price line does not, and the two are
# rounded independently. 12% is wide enough not to reject a correct
# consolidated table and narrow enough to reject a segment one, which differs
# from consolidated by a multiple rather than by a margin.
REVENUE_TOLERANCE = 0.12


def to_boe(value: float, unit: str) -> float | None:
    factor = _TO_BOE.get(unit)
    return None if factor is None else value * factor


def volumes_close(readings: list[ProductionReading]) -> dict[str, bool]:
    """Per period: does oil + NGL + gas/6 equal the printed total?

    The one check a production table can run against itself. It confirms the
    column axis was read correctly — a row matched to the wrong year, or a unit
    taken from the wrong header, breaks it — and it costs nothing, because
    every S-K 1204 table prints the components and the equivalent total.

    A period missing either the components or the total is absent from the
    result rather than False: untested is not failed.
    """
    by_period: dict[str, dict[str | None, float]] = {}
    for r in readings:
        if r.concept_key != VOLUME:
            continue
        boe = to_boe(r.value, r.unit)
        if boe is None:
            continue
        by_period.setdefault(r.period_end, {}).setdefault(r.product, boe)

    out: dict[str, bool] = {}
    for period, products in by_period.items():
        total = products.get(None)
        components = [v for k, v in products.items() if k is not None]
        if total is None or not components or total == 0:
            continue
        out[period] = abs(sum(components) - total) / total <= VOLUME_TOLERANCE
    return out


def implied_revenue(readings: list[ProductionReading], period_end: str) -> float | None:
    """Total volume x realized price for one period, in dollars.

    The external check, and the one that tells a consolidated table from a
    segment table. Both reconcile against themselves — Talos's Gulf of Mexico
    table closes the BOE identity exactly as its consolidated table does — so
    the only thing that separates them is agreement with a consolidated figure
    the filer reported elsewhere. Revenue is that figure, and XBRL has it for
    71 of the 91 cohort members.

    The unhedged price is used where the filer prints both: revenue as reported
    is realized revenue, and mixing the two bases is the comparison this store
    exists to refuse.
    """
    def pick(candidates: list[ProductionReading]) -> ProductionReading | None:
        if not candidates:
            return None
        return next((c for c in candidates if c.is_hedged is False), candidates[0])

    volumes = {
        r.product: r for r in readings
        if r.concept_key == VOLUME and r.period_end == period_end
    }
    prices = {}
    for r in readings:
        if r.concept_key == PRICE and r.period_end == period_end:
            prices.setdefault(r.product, []).append(r)

    # Per product where both sides have it: a filer that prints the components
    # but no equivalent-total price -- which is most of them -- still
    # reconciles this way, and the equivalent row was the only path before.
    total = 0.0
    matched = 0
    for product, volume in volumes.items():
        if product is None:
            continue
        price = pick(prices.get(product, []))
        if price is None:
            continue
        # A price is quoted per barrel or per Mcf, so the volume has to be in
        # the matching unit -- not in BOE, which is what the total row uses.
        units = _PRICE_VOLUME.get(price.unit)
        if units is None:
            continue
        factor = _TO_BOE.get(volume.unit)
        base = _TO_BOE.get(units)
        if factor is None or base is None:
            continue
        total += volume.value * (factor / base) * price.value
        matched += 1
    if matched:
        return total

    # Fall back to the equivalent row where the components are unavailable.
    equivalent = volumes.get(None)
    price = pick(prices.get(None, []))
    if equivalent is None or price is None:
        return None
    boe = to_boe(equivalent.value, equivalent.unit)
    return None if boe is None else boe * price.value
