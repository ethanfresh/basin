"""Read reserve quantities out of the reserve table, where they actually are.

The reserve *quantity* disclosure is required of every producer by Regulation
S-K Subpart 1200, and for most filers it is not in XBRL. It sits in Item 2
(Properties) and in the supplemental oil and gas note — outside the financial
statements, and therefore outside the detail-tagging requirement. Measured
across the 94 cached ``companyfacts`` payloads: 41 filers tag a proved
developed volume, 43 tag no reserve volume at all while tagging between 7 and
50 ``FutureNetCashFlows...`` items from the standardized measure note, and 10
are shells with nothing. SM Energy's FY2025 10-K carries 2,942 inline-tagged
facts and zero ``ProvedDeveloped*`` among them, while printing

    Net proved developed reserves:
      Beginning of year   160.3   1,031.3   71.8   404.0
      End of year         163.7   1,069.7   70.3   412.3

on the page. The number is public; it is simply not tagged. This module reads
it from the table.

Two properties make this safer than it sounds, and both come from reading the
table as a table (:mod:`basin.documents.tables`) rather than as a line of text:

* **The unit is the column header.** Not inferred, not carried in from
  elsewhere. A figure under ``(Bcf)`` is in Bcf.
* **There is no scale to resolve.** The figure is the figure as printed, so
  the factor is 1 by construction. That is the failure mode this path does not
  have: Range's oil reserves are tagged ``21,290`` with unit ``MMBbls`` in its
  own inline XBRL — a thousand-fold error that reads as 21.3 billion BOE — and
  the same column in the same table reads ``(MBbls)``.

What this module will not do is guess. A table whose columns cannot be aligned
to headers, or whose rollforward arithmetic does not close, yields nothing. A
missing row is a coverage gap and shows as one; a wrong row is the failure the
store exists to prevent.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from basin.documents.tables import Table, parse_tables

# Row labels that open a reserve category. Filers vary the qualifier freely
# ("Net proved developed reserves", "Proved developed reserves", "Developed
# reserves"), so the qualifier is optional and the two content words carry the
# match. Anchored at the start: "conversion of proved undeveloped reserves to
# proved developed reserves" is prose in the same document and must not match.
#
# Some filers never print a proved developed line at all: Continental splits it
# into "Proved developed producing" and "Proved developed non-producing" and
# prints only those two, so matching the category prefix files PDP alone as the
# whole of proved developed — 953,343 MBoe where the answer is 959,785. The two
# components are recognised first, and summed only when their sum can be
# checked against the same table's total.
_COMPONENT = re.compile(
    r"(?i)^\(?\d?\)?\s*(?:net\s+|total\s+|estimated\s+)*proved\s+developed"
    r"[\s,]+(?:producing|non[\s-]?producing|shut[\s-]?in|behind[\s-]pipe)"
)

_CATEGORY_PATTERNS: tuple[tuple[str, re.Pattern[str]], ...] = (
    (
        "proved_developed_reserves_boe",
        # "Proved developed AND undeveloped reserves" is the total, not the
        # developed figure -- it is the phrase the standard XBRL element is
        # named after (ProvedDevelopedAndUndevelopedReserveNet), and it is how
        # most filers head the rollforward table. Without the lookahead
        # Diamondback's FY2025 total of 3,617,856 MBoe is read as its developed
        # reserves, collides with the real 2,521,028 from the table that says
        # "proved developed", and the conflict rule drops the cell -- turning a
        # complete disclosure that closes exactly into a hole.
        re.compile(
            r"(?i)^\(?\d?\)?\s*(?:net\s+|total\s+|estimated\s+)*proved\s+developed"
            r"(?!\s+and\s+undeveloped)(?:\s+reserves)?\b"
        ),
    ),
    (
        "proved_undeveloped_reserves_boe",
        re.compile(r"(?i)^\(?\d?\)?\s*(?:net\s+|total\s+|estimated\s+)*proved\s+undeveloped(?:\s+reserves?)?\b"),
    ),
    (
        "proved_reserves_boe",
        re.compile(
            r"(?i)^\(?\d?\)?\s*(?:net\s+|total\s+|estimated\s+)*(?:total\s+)?proved"
            r"(?:\s+developed\s+and\s+undeveloped)?(?:\s+reserves?)?"
            r"(?:,?\s+net)?\s*[:.]?\s*$"
        ),
    ),
)

# Rows inside a category block that carry the closing balance. "End of year"
# is the rollforward form; a bare date is the form Antero and Expand use, and
# it names its own period, which is why it is read rather than assumed.
_CLOSING = re.compile(r"(?i)^\s*(?:balance,?\s*)?(?:end|close|closing)\s+of\s+(?:the\s+)?(?:year|period)\b")
_OPENING = re.compile(r"(?i)^\s*(?:balance,?\s*)?(?:beginning|begin|opening)\s+of\s+(?:the\s+)?(?:year|period)\b")

_MONTHS = (
    "january|february|march|april|may|june|july|august|september|october"
    "|november|december"
)
_DATE = re.compile(rf"(?i)\b({_MONTHS})\s+(\d{{1,2}}),?\s+((?:19|20)\d{{2}})\b")
_MONTH_NUMBER = {
    name: index
    for index, name in enumerate(_MONTHS.split("|"), start=1)
}

# Rollforward movement rows. Named so they can be *excluded*: "Production" and
# "Revisions of previous estimates" sit between the opening and closing
# balances and are flows, not reserves.
_MOVEMENT = re.compile(
    r"(?i)^\s*(?:revisions?|discover|extensions?|additions?|sales?|purchases?"
    r"|production|divestitures?|acquisitions?|transfers?|conversions?"
    r"|improved recovery|infill|net change)"
)

# A column naming more than one product at once. EQT reports "NGLs and Oil
# (MMbbl)" as a single column, and reading it as either component is wrong by
# the other: its FY2025 figure of 224 is NGLs of 215.3 plus oil of 8.6, which
# is why the first cross-check against tagged filers showed an unexplained
# 1.04x on every EQT NGL row. Such a column is dropped; the gas and equivalent
# columns beside it are unambiguous and are kept.
_COMBINED = re.compile(
    r"(?i)\b(?:ngls?|oil|gas|liquids|condensate|bitumen)\b[^()]*?"
    r"(?:\band\b|&|/)[^()]*?\b(?:ngls?|oil|gas|liquids|condensate)\b"
)
AMBIGUOUS = "?"
"""Sentinel product for a column naming several products at once."""

# Column headers, as filers print them. The product word and the unit often
# live on different header rows ("Oil" above "(MMBbl)"), so both forms are
# recognised and a label carrying both at once still works.
_PRODUCT_WORDS: tuple[tuple[str, re.Pattern[str]], ...] = (
    ("ngl", re.compile(r"(?i)\bngls?\b|natural\s+gas\s+liquids?")),
    ("gas", re.compile(r"(?i)\b(?:natural\s+)?gas\b|\bmcf|\bbcf|\bcubic\s+feet")),
    ("oil", re.compile(r"(?i)\boil\b|\bcrude\b|\bcondensate\b|\bliquids?\b")),
)
# The equivalent column is the aggregate, and must be tested before the
# product words: "Total (MMBOE)" contains no product word, but "Oil
# Equivalent" contains one and is not oil.
_EQUIVALENT = re.compile(
    r"(?i)\btotal\b|\bequivalent\b|\bcombined\b|\bboe\b|\bmboe\b|\bmmboe\b"
    r"|\bmcfe\b|\bmmcfe\b|\bbcfe\b"
)

# Unit spellings seen in filing headers, mapped to the store's vocabulary
# (:mod:`basin.facts.units`). Case is not reliable -- "MMBOE", "MMBoe" and
# "MMboe" all occur -- so matching is case-insensitive and the canonical
# spelling is chosen here.
_UNITS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)^mmboe$|^mm\s*boe$"), "MMBoe"),
    (re.compile(r"(?i)^mboe$|^m\s*boe$"), "MBoe"),
    (re.compile(r"(?i)^boe$"), "Boe"),
    (re.compile(r"(?i)^mmbbls?$|^mm\s*bbls?$"), "MMBbls"),
    (re.compile(r"(?i)^mbbls?$|^m\s*bbls?$"), "MBbls"),
    (re.compile(r"(?i)^bbls?$"), "bbl"),
    (re.compile(r"(?i)^bcfe$"), "Bcfe"),
    (re.compile(r"(?i)^bcf$"), "Bcf"),
    (re.compile(r"(?i)^mmcfe$"), "MMcfe"),
    (re.compile(r"(?i)^mmcf$"), "MMcf"),
    (re.compile(r"(?i)^mcfe$"), "Mcfe"),
    (re.compile(r"(?i)^mcf$"), "Mcf"),
)
_UNIT_IN_LABEL = re.compile(
    r"(?i)\(?\b(MMBOE|MBOE|BOE|MMBbls?|MBbls?|Bbls?|Bcfe|Bcf|MMcfe|MMcf|Mcfe|Mcf)\b\)?"
)

# Units written out in words. The majors do not abbreviate: ExxonMobil heads
# its columns "(millions of barrels)" and "(billions of cubic feet)", and
# reading those as unitless is why its tables returned nothing at all.
_SPELLED: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"(?i)million[s]?\s+(?:of\s+)?(?:oil[- ]equivalent\s+)?barrels?\s+of\s+oil\s+equivalent"), "MMBoe"),
    (re.compile(r"(?i)million[s]?\s+(?:of\s+)?oil[- ]equivalent\s+barrels?"), "MMBoe"),
    (re.compile(r"(?i)thousand[s]?\s+(?:of\s+)?barrels?\s+of\s+oil\s+equivalent"), "MBoe"),
    (re.compile(r"(?i)million[s]?\s+(?:of\s+)?(?:bbls?|barrels?)"), "MMBbls"),
    (re.compile(r"(?i)thousand[s]?\s+(?:of\s+)?(?:bbls?|barrels?)"), "MBbls"),
    (re.compile(r"(?i)billion[s]?\s+(?:of\s+)?cubic\s+(?:feet|ft)\s+equivalent"), "Bcfe"),
    (re.compile(r"(?i)billion[s]?\s+(?:of\s+)?cubic\s+(?:feet|ft)"), "Bcf"),
    (re.compile(r"(?i)million[s]?\s+(?:of\s+)?cubic\s+(?:feet|ft)\s+equivalent"), "MMcfe"),
    (re.compile(r"(?i)million[s]?\s+(?:of\s+)?cubic\s+(?:feet|ft)"), "MMcf"),
    (re.compile(r"(?i)thousand[s]?\s+(?:of\s+)?cubic\s+(?:feet|ft)\s+equivalent"), "Mcfe"),
    (re.compile(r"(?i)thousand[s]?\s+(?:of\s+)?cubic\s+(?:feet|ft)"), "Mcf"),
)

_NUMBER = re.compile(r"^[\(\-]?\s*[\d,]+(?:\.\d+)?\s*\)?$")
_DASH = re.compile(r"^[—–\-]$")


@dataclass(frozen=True)
class ReserveReading:
    """One reserve quantity, with the coordinates that make it checkable."""

    concept_key: str
    product: str | None
    """oil / gas / ngl, or None for the equivalent (total) column."""

    value: float
    unit: str
    period_end: str

    table_index: int
    row_label: str
    column_label: str
    source_span: str
    """The row as printed, verbatim. Present in the document by construction."""

    char_offset: int
    """Offset of the containing table in the raw document."""


def _canonical_unit(text: str) -> str | None:
    stripped = text.strip().strip("()").strip()
    for pattern, canonical in _UNITS:
        if pattern.match(stripped):
            return canonical
    found = _UNIT_IN_LABEL.search(text)
    if found:
        for pattern, canonical in _UNITS:
            if pattern.match(found.group(1)):
                return canonical
    for pattern, canonical in _SPELLED:
        if pattern.search(text):
            return canonical
    return None


def _product(text: str) -> str | None:
    """The product a column header names, or None for an equivalent total.

    None is a real answer here, not a failure: the aggregate column is the one
    most panels want. Callers distinguish the two by asking whether a unit was
    resolved at all.
    """
    if _COMBINED.search(text):
        return AMBIGUOUS
    if _EQUIVALENT.search(text):
        return None
    for product, pattern in _PRODUCT_WORDS:
        if pattern.search(text):
            return product
    return None


def _labels(row: list[str]) -> list[str]:
    return [cell for cell in row if cell]


def _column_axis(table: Table) -> list[tuple[str | None, str]] | None:
    """``(product, unit)`` per value column, or None if they cannot be aligned.

    Header rows omit the leading label cell that data rows carry, and pad with
    empties around merged cells, so raw column indices do not line up between
    header and body. The *sequence* of non-empty header labels does, which is
    the same alignment :mod:`basin.documents.tables` makes for `column_labels`.

    Refusing (returning None) is the point of the return type. A table whose
    product row has four labels and whose unit row has three is a table whose
    columns cannot be trusted, and a wrong unit here is a thousand-fold error
    downstream.
    """
    units_row: list[str] | None = None
    products_row: list[str] | None = None
    for row in table.header_rows:
        labels = _labels(row)
        if len(labels) < 2:
            continue
        units = [_canonical_unit(label) for label in labels]
        if units_row is None and sum(1 for u in units if u) >= 2:
            units_row = labels
            continue
        if products_row is None and sum(
            1 for label in labels if _product(label) or _EQUIVALENT.search(label)
        ) >= 2:
            products_row = labels

    if units_row is None:
        return None

    units = [_canonical_unit(label) for label in units_row]
    if not all(units):
        return None

    if products_row is not None and len(products_row) == len(units_row) + 1:
        # The product row often keeps the stub-column heading that the unit row
        # drops ("Proved Reserves | Crude Oil | Natural Gas Liquids | ..." over
        # "(million bbls) | (million bbls) | ..."). One extra label at the front
        # is that heading, not a column.
        products_row = products_row[1:]

    if products_row is None or len(products_row) != len(units_row):
        # A single header row carrying both ("Oil (MMBbl)") is common, and is
        # exactly the case where the unit row IS the product row.
        products = [_product(label) for label in units_row]
    else:
        products = [_product(label) for label in products_row]

    axis = list(zip(products, [u for u in units if u]))

    # Two columns that resolve to the same product and unit mean the axis was
    # not really read -- ExxonMobil's crude oil, NGL, bitumen and synthetic oil
    # columns are all "(million bbls)", and without product names attached they
    # collapse onto one cell where the last one silently wins. Refuse the table
    # instead. It shows as a coverage gap, which is what it is.
    identified = [pair for pair in axis if pair[0] != AMBIGUOUS]
    if len(set(identified)) != len(identified):
        return None
    return axis


_TOTAL_COLUMN = re.compile(r"(?i)^total\b")


def _single_product_axis(table: Table) -> list[tuple[str | None, str]] | None:
    """Axis for a table that states one product and splits columns another way.

    The second common layout. Barnwell prints four tables — "Oil (Bbls)", "NGL
    (Bbls)", "Natural Gas (Mcf)", "Total Equivalent Reserves (Boe)" — each
    split into Canada, United States and Total. The product is the table's, not
    the column's, and the company figure is the Total column.

    Only that column is read. A per-region reserve figure is a real number but
    it is not the one the panel compares, and emitting several of them under
    one identity would let whichever came last win the cell.
    """
    product_label: str | None = None
    columns: list[str] | None = None
    for row in table.header_rows:
        labels = _labels(row)
        if len(labels) == 1 and _canonical_unit(labels[0]):
            product_label = labels[0] if product_label is None else product_label
        elif len(labels) >= 2 and not any(_canonical_unit(label) for label in labels):
            if any(_TOTAL_COLUMN.match(label) for label in labels):
                columns = labels

    if product_label is None or columns is None:
        return None
    unit = _canonical_unit(product_label)
    if unit is None:
        return None
    product = _product(product_label)
    if product == AMBIGUOUS:
        return None
    return [
        (product, unit) if _TOTAL_COLUMN.match(label) else (AMBIGUOUS, unit)
        for label in columns
    ]


def _table_period(table: Table, raw: str, fallback: str | None) -> str | None:
    """The fiscal year end this table reports, read from the table itself.

    SM Energy prints three rollforwards side by side, each headed "For the
    Year Ended December 31, 2025 / 2024 / 2023". Taking the filing's own
    fiscal year for all three would file two years of history under the wrong
    date, so the header is read first and the filing's year is only a fallback.
    """
    for row in table.header_rows:
        for label in row:
            found = _DATE.search(label)
            if found:
                return _iso(found)
    preceding = raw[max(0, table.start - 1200) : table.start]
    matches = list(_DATE.finditer(re.sub(r"<[^>]+>", " ", preceding)))
    if matches:
        return _iso(matches[-1])
    return fallback


def _iso(match: re.Match[str]) -> str:
    month = _MONTH_NUMBER[match.group(1).lower()]
    return f"{int(match.group(3)):04d}-{month:02d}-{int(match.group(2)):02d}"


def _prior_year(period_end: str) -> str:
    return f"{int(period_end[:4]) - 1:04d}{period_end[4:]}"


def _row_period(label: str, table_period: str) -> str | None:
    """The period a row inside a category block reports.

    A row naming its own date wins over the table's, because filers who use
    that form stack several years in one block.
    """
    found = _DATE.search(label)
    if found:
        return _iso(found)
    if _CLOSING.match(label):
        return table_period
    if _OPENING.match(label):
        # The opening balance is the prior year's close. It is emitted because
        # it is a real disclosure of a real period, and because it gives the
        # arithmetic check something to close against.
        return _prior_year(table_period)
    return None


def _values(cells: list[tuple[int, str]]) -> list[str] | None:
    """The value cells of a row, in order, with dashes kept as placeholders.

    Dashes matter for alignment and are the reason ``Cell.header`` cannot be
    used directly: a row reading ``(3.2) (13.1) — (5.4)`` has four columns,
    and dropping the dash shifts every header after it by one. In SM Energy's
    FY2023 table that mislabels the total column ``(MMBbl)`` when it is
    ``(MMBOE)``.
    """
    values: list[str] = []
    for _, text in cells:
        if _NUMBER.match(text) or _DASH.match(text):
            values.append(text)
        elif values:
            # Trailing prose after the figures (a footnote marker) is fine;
            # prose *between* them means this is not a clean value row.
            return values or None
    return values or None


def _number(text: str) -> float | None:
    if _DASH.match(text):
        return 0.0
    cleaned = text.replace(",", "").replace("(", "-").replace(")", "").strip()
    try:
        return float(cleaned)
    except ValueError:
        return None


def _rows(table: Table) -> list[tuple[int, str, list[tuple[int, str]]]]:
    """``(row number, label, cells)`` in document order."""
    grouped: dict[int, list[tuple[int, str]]] = {}
    for cell in table.cells:
        grouped.setdefault(cell.row, []).append((cell.column, cell.text))
    out = []
    for row in sorted(grouped):
        cells = sorted(grouped[row])
        label = next(
            (text for _, text in cells if text and not _NUMBER.match(text) and not _DASH.match(text)),
            "",
        )
        out.append((row, label, cells))
    return out


def readings_for_table(
    table: Table, raw: str, *, fallback_period: str | None = None
) -> list[ReserveReading]:
    """Every reserve quantity in one table, or nothing if it cannot be read."""
    axis = _column_axis(table) or _single_product_axis(table)
    if axis is None:
        return []
    period = _table_period(table, raw, fallback_period)
    if period is None:
        return []

    readings: list[ReserveReading] = []

    # The first category header is frequently absorbed into the header block:
    # "Total net proved reserves:" is non-numeric and sits above the data, so
    # `parse_tables` reads it as a header row, which it also is. Seeding the
    # category from it is what puts SM Energy's total proved rollforward in the
    # store instead of dropping seven rows per table on the floor.
    category: str | None = None
    for row in table.header_rows:
        for key, pattern in _CATEGORY_PATTERNS:
            if any(pattern.match(label) for label in row if label):
                category = key

    # Components of proved developed, accumulated per period and column, to be
    # summed after the walk if the filer printed no developed line of its own.
    components: dict[str, list[list[str]]] = {}
    component_labels: list[str] = []

    for _, label, cells in _rows(table):
        values = _values(cells)

        if _COMPONENT.match(label):
            if values:
                row_period = _row_period(label, period) or period
                components.setdefault(row_period, []).append(values)
                component_labels.append(label)
            continue

        matched = next(
            (key for key, pattern in _CATEGORY_PATTERNS if pattern.match(label)), None
        )

        if matched is not None and not values:
            # A category header with no figures opens a block; the rows under
            # it belong to it until the next one.
            category = matched
            continue

        if matched is not None and values:
            # The flat form: the category and its figures on one row. The label
            # may name its own date -- Barnwell writes "Proved Developed
            # Reserves, September 30, 2023" and "...2024" as two rows of one
            # table -- and taking the table's period for both files two
            # different figures under one cell.
            readings.extend(
                _emit(
                    matched,
                    label,
                    values,
                    axis,
                    _row_period(label, period) or period,
                    table,
                    cells,
                )
            )
            continue

        if category is None or not values or _MOVEMENT.match(label):
            continue

        row_period = _row_period(label, period)
        if row_period is None:
            continue
        readings.extend(
            _emit(category, label, values, axis, row_period, table, cells)
        )

    have_developed = {
        r.period_end
        for r in readings
        if r.concept_key == "proved_developed_reserves_boe"
    }
    for row_period, rows in components.items():
        if row_period in have_developed or len(rows) < 2:
            continue
        if any(len(row) != len(axis) for row in rows):
            continue
        summed = []
        for column in range(len(axis)):
            numbers = [_number(row[column]) for row in rows]
            summed.append("" if any(n is None for n in numbers) else str(sum(numbers)))
        if not all(summed):
            continue
        readings.extend(
            _emit(
                "proved_developed_reserves_boe",
                " + ".join(component_labels),
                summed,
                axis,
                row_period,
                table,
                [(0, " + ".join(component_labels))],
            )
        )

    return readings


def _emit(
    concept_key: str,
    label: str,
    values: list[str],
    axis: list[tuple[str | None, str]],
    period_end: str,
    table: Table,
    cells: list[tuple[int, str]],
) -> list[ReserveReading]:
    """Pair a row's figures with the column axis, or emit nothing.

    Length mismatch is a refusal rather than a zip-to-shortest, which would
    silently attach the NGL figure to the total column for any row carrying an
    extra footnote figure.
    """
    if len(values) != len(axis):
        return []
    span = " ".join(text for _, text in cells if text)
    out = []
    for (product, unit), text in zip(axis, values):
        if product is AMBIGUOUS or product == AMBIGUOUS:
            continue
        number = _number(text)
        if number is None or number < 0:
            continue
        out.append(
            ReserveReading(
                concept_key=concept_key,
                product=product,
                value=number,
                unit=unit,
                period_end=period_end,
                table_index=table.index,
                row_label=label,
                column_label=f"{product or 'total'} ({unit})",
                source_span=span[:400],
                char_offset=table.start,
            )
        )
    return out


def reserve_readings(
    raw: str, *, fallback_period: str | None = None
) -> list[ReserveReading]:
    """Every reserve quantity in a filing, from every table that carries one.

    The same figures appear twice in most 10-Ks — once in Item 2 and once in
    the supplemental oil and gas note — and both are returned. They agree, and
    a caller that wants one cell per period deduplicates on the identity the
    store already uses.
    """
    readings: list[ReserveReading] = []
    for table in parse_tables(raw):
        # Headers as well as cells: a category row that is entirely
        # non-numeric ("Proved Developed Reserves:") is classified as a header
        # by the table parser and never appears in ``cells``, so a cells-only
        # test skips the table it names.
        labels = [cell.text for cell in table.cells]
        labels.extend(text for row in table.header_rows for text in row)
        if not any(
            pattern.match(text) for text in labels for _, pattern in _CATEGORY_PATTERNS
        ):
            continue
        readings.extend(
            readings_for_table(table, raw, fallback_period=fallback_period)
        )
    return readings
