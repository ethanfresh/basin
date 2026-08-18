"""Parse filing tables as tables, keeping each cell's column header.

Flattening a table to a line of numbers throws away the thing that gives a
number meaning. Gulfport's reserve table reads

    Oil (MMBbl)   Natural Gas (Bcf)   NGL (MMBbl)   Total (Bcfe)
    Total proved       19                  2,906           52          3,328

and flattened, ``3,328`` is a bare number whose unit sits forty characters
earlier with no structural connection to it. That is how Gulfport's reserves
came to be stored as 4.25 billion barrels when the correct figure is 708.8
million -- a sixfold error that a column header would have prevented (D2).

Header detection is positional rather than tag-based: filings use ``<td>``
where they mean ``<th>`` often enough that trusting the tag misses most
headers. A row counts as a header while its cells are mostly non-numeric, and
the last such row before the data governs the columns beneath it.
"""

from __future__ import annotations

import html as html_module
import re
from dataclasses import dataclass, field

_TABLE = re.compile(r"(?is)<table\b[^>]*>(.*?)</table>")
_ROW = re.compile(r"(?is)<tr\b[^>]*>(.*?)</tr>")
_CELL = re.compile(r"(?is)<t[dh]\b([^>]*)>(.*?)</t[dh]>")
_COLSPAN = re.compile(r'(?i)colspan="?(\d+)"?')
_TAG = re.compile(r"<[^>]+>")
_WS = re.compile(r"[\s  ​]+")
_NUMERIC = re.compile(r"^[\(\)\-\+\$\s]*[\d,][\d,.\s]*[\)%]?$")


def _clean(fragment: str) -> str:
    return _WS.sub(" ", html_module.unescape(_TAG.sub(" ", fragment))).strip()


@dataclass(frozen=True)
class Cell:
    """One cell, and the headers standing above it."""

    row: int
    column: int
    text: str
    header: str = ""
    row_label: str = ""

    @property
    def is_numeric(self) -> bool:
        return bool(self.text) and bool(_NUMERIC.match(self.text))


@dataclass
class Table:
    """One table, with headers resolved onto its cells."""

    index: int
    start: int
    end: int
    header_rows: list[list[str]] = field(default_factory=list)
    cells: list[Cell] = field(default_factory=list)

    @property
    def column_labels(self) -> list[str]:
        """Header labels in order, ignoring the empty padding cells.

        Header rows omit the leading label cell that data rows carry ("Total
        proved"), so raw column indices do not line up between them. Comparing
        the *sequence* of labels against the sequence of numeric cells does.
        """
        for row in reversed(self.header_rows):
            labels = [c for c in row if c]
            if len(labels) > 1:
                return labels
        return []

    def column_header(self, column: int) -> str:
        for row in reversed(self.header_rows):
            if column < len(row) and row[column]:
                return row[column]
        return ""


def _row_cells(row_html: str) -> list[str]:
    """Cells of a row, expanded so colspan keeps columns aligned."""
    out: list[str] = []
    for attrs, body in _CELL.findall(row_html):
        text = _clean(body)
        span = _COLSPAN.search(attrs)
        width = int(span.group(1)) if span else 1
        out.append(text)
        out.extend("" for _ in range(width - 1))
    return out


def _is_header_row(cells: list[str]) -> bool:
    """A header row is mostly words; a data row is mostly numbers."""
    filled = [c for c in cells if c]
    if not filled:
        return False
    numeric = sum(1 for c in filled if _NUMERIC.match(c))
    return numeric <= len(filled) // 3


def parse_tables(raw: str) -> list[Table]:
    """Every table in a document, with column headers attached to cells."""
    tables: list[Table] = []
    for index, match in enumerate(_TABLE.finditer(raw)):
        rows = [_row_cells(r) for r in _ROW.findall(match.group(1))]
        if not rows:
            continue

        table = Table(index=index, start=match.start(), end=match.end())
        seen_data = False
        for row_number, cells in enumerate(rows):
            # A blank row is spacing, not data. Letting it flip `seen_data`
            # meant every header row after it was read as data, which is why
            # column headers came back empty for tables that open with one.
            if not any(cells):
                continue
            if not seen_data and _is_header_row(cells):
                table.header_rows.append(cells)
                continue
            seen_data = True
            # The leading non-numeric cell names the row ("Total proved").
            label = next((c for c in cells if c and not _NUMERIC.match(c)), "")
            labels = table.column_labels
            numeric_position = 0
            for column, text in enumerate(cells):
                if not text:
                    continue
                if _NUMERIC.match(text):
                    header = (
                        labels[numeric_position]
                        if numeric_position < len(labels)
                        else table.column_header(column)
                    )
                    numeric_position += 1
                else:
                    header = table.column_header(column)
                table.cells.append(
                    Cell(
                        row=row_number,
                        column=column,
                        text=text,
                        header=header,
                        row_label=label,
                    )
                )
        if table.cells:
            tables.append(table)
    return tables


def header_for_value(tables: list[Table], printed: str) -> tuple[str, str] | None:
    """``(column header, row label)`` for a printed figure, if it is in a table.

    Used as a cross-check on the unit a fact claims: a value sitting under
    ``Total (Bcfe)`` is not in barrels, whatever the tagging says.
    """
    target = printed.strip()
    for table in tables:
        for cell in table.cells:
            if cell.text == target:
                return cell.header, cell.row_label
    return None
