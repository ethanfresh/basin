"""Check that a stored value actually appears in the filing it cites.

The README's rule is that a citation is not done until the cited text has been
found in the cited document. Everything in the store so far came from the XBRL
API, where the accession is asserted by the SEC rather than confirmed by
Basin, so nothing had been checked against the document itself.

Checking also answers a question XBRL cannot. A filer's declared unit does not
determine the magnitude -- Diamondback's proved reserves arrive as
2,521,028,000 tagged MBoe, while the 10-K's reserve table prints 2,521,028 --
so the document is the only place the *presentation scale* is stated. A
verified match therefore carries the scale it matched at, which is the missing
piece the peer panel needs before it can rank across filers.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from basin.documents.text import snippet

# Scales worth trying, as (factor, label). A document value equal to the
# stored value divided by the factor means the filing presents the figure in
# those units.
SCALES: tuple[tuple[float, str], ...] = (
    (1.0, "as tagged"),
    (1e3, "thousands"),
    (1e6, "millions"),
    (1e9, "billions"),
    (1e-3, "tagged in thousands of the printed unit"),
)

MAX_HITS_FOR_CONFIDENCE = 3


@dataclass(frozen=True)
class Match:
    """Where a value was found, and at what scale."""

    printed: str
    """The literal string matched in the document."""

    scale: float
    scale_label: str
    offset: int
    hits: int
    source_span: str

    @property
    def unambiguous(self) -> bool:
        """A number appearing all over the filing proves less than a rare one."""
        return self.hits <= MAX_HITS_FOR_CONFIDENCE


def _candidates(value: float) -> list[tuple[str, float, str]]:
    """Printed forms of *value* to look for, most specific first."""
    out: list[tuple[str, float, str]] = []
    seen: set[str] = set()
    for factor, label in SCALES:
        scaled = value / factor
        forms: list[str] = []
        if abs(scaled) >= 1 and abs(scaled - round(scaled)) < 0.5:
            forms.append(f"{round(scaled):,}")
        if abs(scaled) < 1e6:
            forms.append(f"{scaled:,.1f}")
            forms.append(f"{scaled:,.2f}")
        for form in forms:
            # A one- or two-digit string matches half the document; it proves
            # nothing, so it is not worth searching for.
            if len(form.replace(",", "").replace(".", "").lstrip("-")) < 3:
                continue
            if form in seen:
                continue
            seen.add(form)
            out.append((form, factor, label))
    # Longer strings are more specific, so they are tried first.
    out.sort(key=lambda t: -len(t[0]))
    return out


def find_value(text: str, value: float) -> Match | None:
    """Locate *value* in *text*, trying the scales filings actually use.

    Returns the most specific match found, or None. A match is evidence the
    figure is in the document; ``unambiguous`` says whether it is evidence
    worth much.
    """
    for printed, factor, label in _candidates(value):
        positions = [m.start() for m in re.finditer(re.escape(printed), text)]
        # Reject a hit that is really part of a longer number.
        positions = [
            p
            for p in positions
            if not (p and (text[p - 1].isdigit() or text[p - 1] in ",."))
            and not (
                p + len(printed) < len(text)
                and (text[p + len(printed)].isdigit() or text[p + len(printed)] in ",")
            )
        ]
        if not positions:
            continue
        start = positions[0]
        return Match(
            printed=printed,
            scale=factor,
            scale_label=label,
            offset=start,
            hits=len(positions),
            source_span=snippet(text, start, start + len(printed)),
        )
    return None
