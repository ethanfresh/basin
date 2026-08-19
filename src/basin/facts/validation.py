"""Per-filer alias validation for the reserve family.

Picking each concept's XBRL tag independently, by a global preference order,
assumes a tag means the same thing for every filer. It does not. Continental
tags ``ProvedDevelopedAndUndevelopedReserveNetEnergy`` at 745 thousand MBoe
while its developed and undeveloped reserves sum to 2.68 million -- the tag
carries a product-dimensioned figure there, and the companyfacts API has
already flattened the dimension away, so nothing in the payload says so.

What does say so is arithmetic. Proved developed plus proved undeveloped
equals total proved, by definition, in every filing. That identity is a test a
*combination* of tags either passes or fails, which turns alias selection from
a guess into a measurement: enumerate the candidate tags a filer actually
uses, try the combinations, and keep the one whose numbers agree.

Where no combination agrees, this reports that rather than picking a winner.
A silently wrong total is worse than an absent one.

The identity is tested per period, and the answer is kept per period. A tag can
mean one thing for a decade and something else afterwards -- Continental's
``ProvedUndevelopedReserveBOE1`` carries the undeveloped figure through FY2017
and the *total* from FY2018 on, with ``ProvedDevelopedAndUndevelopedReserveNet-
Energy`` carrying undeveloped in exchange. No single combination is right for
its whole history, so choosing one and applying it everywhere writes the swap
into five years of cells. ``coherent_period_ends`` is what the ingest filters
on, so the periods where the chosen combination fails its own arithmetic are
not written at all.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field
from itertools import product
from typing import Any, Iterable

from basin.facts.concepts import (
    RESERVES_DEVELOPED,
    RESERVES_TOTAL_PROVED,
    RESERVES_UNDEVELOPED,
    ConceptSpec,
)

# developed + undeveloped = total
RESERVE_FAMILY: tuple[ConceptSpec, ConceptSpec, ConceptSpec] = (
    RESERVES_DEVELOPED,
    RESERVES_UNDEVELOPED,
    RESERVES_TOTAL_PROVED,
)

# Filings round the three figures independently, so they rarely sum exactly.
# 3% is loose enough to absorb that and far tighter than the errors this is
# built to catch, which run to factors of a thousand.
TOLERANCE = 0.03

STATUS_VALIDATED = "validated"
STATUS_DRIFTED = "drifted"
STATUS_INCOHERENT = "incoherent"
STATUS_INSUFFICIENT = "insufficient"


@dataclass(frozen=True)
class Candidate:
    """One (tag, unit) series a filer actually reports for one concept."""

    concept_key: str
    taxonomy: str
    tag: str
    unit: str
    values: dict[str, float] = field(compare=False, default_factory=dict)


@dataclass(frozen=True)
class FamilyValidation:
    """The chosen alias per concept, and the evidence for choosing it."""

    cik: str
    status: str
    choices: dict[str, Candidate]
    tested_periods: int = 0
    coherent_periods: int = 0
    median_error: float | None = None
    note: str = ""
    coherent_period_ends: frozenset[str] = frozenset()
    """Periods where developed + undeveloped = total actually held.

    Empty when nothing was testable, which is not the same as nothing holding
    -- see ``status``. A caller filtering on this must check ``testable``
    first, or an untested filer loses every reserve row it has.
    """

    incoherent_period_ends: frozenset[str] = frozenset()
    """Periods that were tested and failed. These are the cells to suppress."""

    @property
    def testable(self) -> bool:
        """Whether the identity could be evaluated at all for this filer."""
        return self.tested_periods > 0

    def holds_for(self, period_end: str) -> bool:
        """Whether this filer's reserve family can be trusted for one period.

        A period that was never tested passes: two of the three concepts
        untagged is a coverage gap, not evidence of a wrong number.
        """
        return period_end not in self.incoherent_period_ends

    @property
    def overrides(self) -> dict[str, tuple[str, str]]:
        """``concept_key -> (taxonomy, tag)`` for the ingest to apply."""
        return {k: (c.taxonomy, c.tag) for k, c in self.choices.items()}

    @property
    def unit_overrides(self) -> dict[str, str]:
        """``concept_key -> unit`` that the arithmetic agreed on."""
        return {k: c.unit for k, c in self.choices.items()}


def candidates_for(
    payload: dict[str, Any], spec: ConceptSpec, *, forms: Iterable[str] | None = ("10-K", "10-K/A")
) -> list[Candidate]:
    """Every (alias, unit) series this filer reports for one concept."""
    facts = payload.get("facts", {})
    wanted = set(forms) if forms is not None else None
    out: list[Candidate] = []

    # Merge taxonomies carrying the same tag name: they are one disclosure
    # split across a taxonomy migration, and treating them as rival candidates
    # would score two halves of one series against each other.
    merged: dict[tuple[str, str], list] = {}
    for taxonomy, tag in spec.aliases:
        entry = facts.get(taxonomy, {}).get(tag)
        if not entry:
            continue
        for unit, observations in entry.get("units", {}).items():
            merged.setdefault((tag, unit), []).extend(observations)

    for (tag, unit), observations in merged.items():
        taxonomy = next(
            tx for tx, tg in spec.aliases if tg == tag and tg in facts.get(tx, {})
        )
        values: dict[str, float] = {}
        filed_at: dict[str, str] = {}
        for obs in observations:
            if wanted is not None and obs.get("form") not in wanted:
                continue
            if "end" not in obs or "val" not in obs or "accn" not in obs:
                continue
            # Later filings restate earlier ones, so keep the newest --
            # comparing each observation against the *stored* value's
            # filing date, not against some fixed element of the list.
            key, filed = obs["end"], obs.get("filed", "")
            if key not in values or filed >= filed_at[key]:
                values[key] = float(obs["val"])
                filed_at[key] = filed
        if values:
            out.append(Candidate(spec.key, taxonomy, tag, unit, values))
    return out


@dataclass(frozen=True)
class Score:
    """How one alias combination fares against the identity, period by period."""

    coherent: frozenset[str]
    incoherent: frozenset[str]
    median_error: float | None

    @property
    def tested(self) -> int:
        return len(self.coherent) + len(self.incoherent)

    @property
    def latest_ok(self) -> bool:
        """Whether the most recent tested period holds.

        Tracked separately from how many periods hold: a tag can mean one thing
        for a decade and something else afterwards, and a panel showing FY2025
        is not helped by agreement in FY2012.
        """
        if not self.tested:
            return False
        return max(self.coherent | self.incoherent) in self.coherent


def _score(dev: Candidate, undev: Candidate, total: Candidate) -> Score:
    """Test developed + undeveloped = total in every period all three cover."""
    periods = sorted(set(dev.values) & set(undev.values) & set(total.values))
    errors: list[float] = []
    coherent: set[str] = set()
    incoherent: set[str] = set()
    for p in periods:
        t = total.values[p]
        if t == 0:
            # Nothing to divide by, so nothing measured. Not a failure.
            continue
        err = abs(dev.values[p] + undev.values[p] - t) / abs(t)
        errors.append(err)
        (coherent if err <= TOLERANCE else incoherent).add(p)
    return Score(
        coherent=frozenset(coherent),
        incoherent=frozenset(incoherent),
        median_error=statistics.median(errors) if errors else None,
    )


def validate_reserve_family(
    payload: dict[str, Any], *, forms: Iterable[str] | None = ("10-K", "10-K/A")
) -> FamilyValidation:
    """Choose the alias combination whose arithmetic holds for this filer.

    Only combinations sharing a single unit are considered. Converting between
    units to force agreement would invent precision the filing does not
    provide, and unit conversion is the very thing that cannot be trusted here.
    """
    cik = str(payload.get("cik", "")).zfill(10)
    by_concept = {
        spec.key: candidates_for(payload, spec, forms=forms) for spec in RESERVE_FAMILY
    }

    if not all(by_concept.values()):
        missing = [k for k, v in by_concept.items() if not v]
        return FamilyValidation(
            cik=cik,
            status=STATUS_INSUFFICIENT,
            choices={},
            note=f"not all three concepts tagged (missing: {', '.join(sorted(missing))})",
        )

    dev_key, undev_key, total_key = (s.key for s in RESERVE_FAMILY)
    best: tuple[tuple[int, float, int], FamilyValidation] | None = None
    tried = 0

    for dev, undev, total in product(
        by_concept[dev_key], by_concept[undev_key], by_concept[total_key]
    ):
        if not (dev.unit == undev.unit == total.unit):
            continue
        score = _score(dev, undev, total)
        if not score.tested:
            continue
        tried += 1
        # A combination that holds in the latest period outranks one that
        # merely holds more often, because the latest period is the one the
        # panel shows. Then most coherent periods, then lowest error.
        rank = (
            score.latest_ok,
            len(score.coherent),
            -(score.median_error if score.median_error is not None else 1e9),
            score.tested,
        )
        candidate = FamilyValidation(
            cik=cik,
            status=(
                STATUS_VALIDATED if score.latest_ok
                else STATUS_DRIFTED if score.coherent
                else STATUS_INCOHERENT
            ),
            choices={dev_key: dev, undev_key: undev, total_key: total},
            tested_periods=score.tested,
            coherent_periods=len(score.coherent),
            median_error=score.median_error,
            coherent_period_ends=score.coherent,
            incoherent_period_ends=score.incoherent,
        )
        if best is None or rank > best[0]:
            best = (rank, candidate)

    if best is None:
        return FamilyValidation(
            cik=cik,
            status=STATUS_INSUFFICIENT,
            choices={},
            note="no combination shares a unit across all three concepts",
        )

    result = best[1]
    if result.status == STATUS_DRIFTED:
        pct = f"{result.median_error:.1%}" if result.median_error is not None else "n/a"
        return FamilyValidation(
            cik=result.cik,
            status=STATUS_DRIFTED,
            choices=result.choices,
            tested_periods=result.tested_periods,
            coherent_periods=result.coherent_periods,
            median_error=result.median_error,
            coherent_period_ends=result.coherent_period_ends,
            incoherent_period_ends=result.incoherent_period_ends,
            note=(
                f"held for {result.coherent_periods} of {result.tested_periods} "
                f"periods but not the most recent; the tag's meaning appears to "
                f"have changed (median error {pct}). "
                f"{len(result.incoherent_period_ends)} period(s) suppressed: "
                + ", ".join(sorted(result.incoherent_period_ends))
            ),
        )
    if result.status == STATUS_INCOHERENT:
        pct = f"{result.median_error:.1%}" if result.median_error is not None else "n/a"
        return FamilyValidation(
            cik=result.cik,
            status=STATUS_INCOHERENT,
            choices=result.choices,
            tested_periods=result.tested_periods,
            coherent_periods=0,
            median_error=result.median_error,
            coherent_period_ends=frozenset(),
            incoherent_period_ends=result.incoherent_period_ends,
            note=(
                f"no combination of {tried} satisfies developed + undeveloped = "
                f"total; best median error {pct}. Every tested period suppressed"
            ),
        )
    return result
