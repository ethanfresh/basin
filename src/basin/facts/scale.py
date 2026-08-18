"""Decide which reading of a stored figure is the real one.

Verifying a value against its filing yields the scale the *document* prints it
at, which produces two candidate magnitudes: the value as tagged, and the
value descaled to what the filing printed. That is not enough to choose
between them, and the two filers below prove it -- both verify at a scale of
1,000, and the correct reading is the opposite one in each case:

    Diamondback  3,617,856,000 MBoe   -> descaled is right (3.6 billion BOE)
    CNX          9,662,144,000 Mcfe   -> as tagged is right (1.6 billion BOE)

An economic identity separates them. The standardized measure is a discounted
present value of the same reserves, in dollars, so dividing one by the other
gives a value per barrel -- and that number has a range no real producer falls
outside. Diamondback reads $10.20/BOE descaled against $0.01 as tagged; CNX
reads $3.15/BOE as tagged against $3,146 descaled. In both cases exactly one
candidate is a price a barrel of oil could have.

This is inference, not measurement, so every resolution records the ratio it
turned on and the alternative it rejected. A cell whose candidates are both
plausible, or both absurd, stays unresolved rather than being guessed at.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from basin.facts.units import conversion_for

# Standardized measure per BOE, in dollars. Gas-weighted producers sit near
# the bottom and oil-weighted ones near the top; the band is deliberately
# wider than reality on both sides, because its job is to separate a right
# answer from one that is wrong by a factor of a thousand, not to be precise.
MIN_USD_PER_BOE = 0.30
MAX_USD_PER_BOE = 120.0

STATUS_RESOLVED = "resolved"
STATUS_AMBIGUOUS = "ambiguous"
STATUS_UNAVAILABLE = "unavailable"


@dataclass(frozen=True)
class Candidate:
    """One reading of a stored value, in canonical units."""

    divisor: float
    canonical_value: float
    label: str


@dataclass(frozen=True)
class Resolution:
    """Which reading was chosen, and the evidence for it."""

    status: str
    reserve_divisor: float | None = None
    measure_divisor: float | None = None
    usd_per_boe: float | None = None
    rejected: str = ""
    note: str = ""


def candidates(value: float, unit: str, scale: float | None) -> list[Candidate]:
    """The distinct readings of *value* implied by its verified scale."""
    conversion = conversion_for(unit)
    if conversion is None:
        return []
    out = [Candidate(1.0, value * conversion.factor, "as tagged")]
    if scale and scale != 1.0:
        out.append(
            Candidate(scale, (value / scale) * conversion.factor, "as printed")
        )
    return out


def resolve(
    reserve_value: float,
    reserve_unit: str,
    reserve_scale: float | None,
    measure_value: float | None,
    measure_scale: float | None,
) -> Resolution:
    """Choose the reserve and standardized-measure readings that agree.

    Agreement means the implied value per barrel is one a producer could
    actually report. Where several combinations qualify, the one closest to
    the middle of the band wins and the rest are recorded as rejected.
    """
    reserves = candidates(reserve_value, reserve_unit, reserve_scale)
    if not reserves:
        return Resolution(STATUS_UNAVAILABLE, note=f"no canonical form for {reserve_unit}")
    if measure_value is None:
        return Resolution(
            STATUS_UNAVAILABLE, note="no standardized measure to test reserves against"
        )

    # The standardized measure anchors the comparison and is never descaled.
    #
    # Monetary facts in XBRL are reported in units of currency; "in thousands"
    # on a financial statement is a presentation convention applied to the
    # printed page, not to the tagged value, and that rule holds far more
    # reliably than anything about volume units. Descaling both sides would
    # also leave the ratio unchanged, so a floating anchor could fix the
    # relative scale while leaving the absolute magnitude undetermined --
    # which is what made CNX look ambiguous.
    measures = [Candidate(1.0, measure_value, "as tagged")]

    viable: list[tuple[float, Candidate, Candidate]] = []
    rejected: list[str] = []
    for r in reserves:
        for m in measures:
            if r.canonical_value <= 0:
                continue
            ratio = m.canonical_value / r.canonical_value
            if MIN_USD_PER_BOE <= ratio <= MAX_USD_PER_BOE:
                viable.append((ratio, r, m))
            else:
                rejected.append(f"{r.label}/{m.label}=${ratio:,.2f}/BOE")

    if not viable:
        return Resolution(
            STATUS_AMBIGUOUS,
            rejected="; ".join(rejected),
            note="no reading implies a plausible value per barrel",
        )

    # Closest to the middle of the band on a log scale, since the candidates
    # differ by orders of magnitude rather than by percentages.
    midpoint = (MIN_USD_PER_BOE * MAX_USD_PER_BOE) ** 0.5
    ratio, reserve, measure = min(viable, key=lambda t: _log_distance(t[0], midpoint))
    note = ""
    if len(viable) > 1:
        note = f"{len(viable)} readings were plausible; chose the closest to ${midpoint:,.0f}/BOE"
    return Resolution(
        STATUS_RESOLVED,
        reserve_divisor=reserve.divisor,
        measure_divisor=measure.divisor,
        usd_per_boe=ratio,
        rejected="; ".join(rejected),
        note=note,
    )


def _log_distance(a: float, b: float) -> float:
    """Distance on a log scale, where the candidates actually differ."""
    return abs(math.log(a) - math.log(b))
