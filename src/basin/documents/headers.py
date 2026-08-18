"""Recover the unit a filing's table actually states.

Scale resolution fixes how big a number is but trusts the *unit family* the
filer tagged, and that label is sometimes simply wrong. Gulfport tags total
proved reserves in ``bbl``; the 10-K's table is headed
``Oil (MMBbl) Natural Gas (Bcf) NGL (MMBbl) Total (Bcfe)`` and the matched
figure sits in the Total column, in Bcfe. Read as barrels it implies
$0.80/BOE; read as Bcfe it implies $4.80, which is what a gas producer looks
like.

Units appear in two shapes, and both are read here:

* inline, next to the figure -- "reserves were 3,617,856 MBOE"
* as a column header above it -- "Total (Bcfe)"

Deliberately *not* done: working out which column a number sits in by counting
across the row. Filings put footnote markers, section labels and subtotal rows
in the middle of tables, so the count drifts and fails silently. Instead every
unit near the figure is offered as a candidate, and the arithmetic that
already resolves scale picks between them -- proposal here, disposal there.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

# Units worth recognising in a filing's prose or table headers.
_UNIT_ALTERNATIVES = [
    "MMBOE", "MMBoe", "MBOE", "MBoe", "BOE", "Boe", "boe",
    "MMBbls", "MMBbl", "MMbbls", "MBbls", "MBbl", "Mbbls", "bbls", "bbl", "Bbl",
    "Bcfe", "Bcf", "MMcfe", "MMcf", "Mcfe", "Mcf", "Tcfe", "Tcf",
]
_UNIT_RE = re.compile(r"\b(" + "|".join(_UNIT_ALTERNATIVES) + r")\b")

# A parenthesised unit is almost always a column header.
_HEADER_RE = re.compile(r"\(\s*(" + "|".join(_UNIT_ALTERNATIVES) + r")\s*(?:/d)?\s*\)")

DEFAULT_LOOKBACK = 2_500
DEFAULT_LOOKAHEAD = 40


@dataclass(frozen=True)
class UnitHint:
    """A unit seen near a figure, and how it was seen."""

    unit: str
    distance: int
    kind: str  # 'inline' | 'header'

    @property
    def confident(self) -> bool:
        """Inline units sit beside the figure and rarely belong to anything else."""
        return self.kind == "inline" and self.distance <= DEFAULT_LOOKAHEAD


def unit_hints(
    text: str,
    offset: int,
    length: int,
    *,
    lookback: int = DEFAULT_LOOKBACK,
    lookahead: int = DEFAULT_LOOKAHEAD,
) -> list[UnitHint]:
    """Units near the figure at *offset*, nearest first.

    Looks a short way forward for an inline unit and a long way back for
    column headers, since a header sits above every row of its table.
    """
    hints: list[UnitHint] = []
    end = offset + length

    tail = text[end : end + lookahead]
    match = _UNIT_RE.search(tail)
    if match and not tail[: match.start()].strip(" ,.:;)"):
        hints.append(UnitHint(match.group(1), match.start(), "inline"))

    head = text[max(0, offset - lookback) : offset]
    for match in _HEADER_RE.finditer(head):
        hints.append(UnitHint(match.group(1), len(head) - match.end(), "header"))

    # Nearest first, and an inline hint outranks a header at equal distance.
    hints.sort(key=lambda h: (h.distance, h.kind != "inline"))

    seen: set[str] = set()
    unique: list[UnitHint] = []
    for hint in hints:
        if hint.unit not in seen:
            seen.add(hint.unit)
            unique.append(hint)
    return unique
