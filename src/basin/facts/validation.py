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


def _score(
    dev: Candidate, undev: Candidate, total: Candidate
) -> tuple[int, int, float | None, bool]:
    """(coherent periods, tested periods, median error, latest is coherent).

    Whether the *latest* period holds is tracked separately from how many
    periods hold. A tag can mean one thing for a decade and something else
    afterwards, and a panel showing FY2025 is not helped by agreement in
    FY2012 -- so the two facts are reported rather than averaged together.
    """
    periods = sorted(set(dev.values) & set(undev.values) & set(total.values))
    errors: list[float] = []
    coherent = 0
    latest_ok = False
    for p in periods:
        t = total.values[p]
        if t == 0:
            continue
        err = abs(dev.values[p] + undev.values[p] - t) / abs(t)
        errors.append(err)
        if err <= TOLERANCE:
            coherent += 1
            latest_ok = True
        else:
            latest_ok = False
    return coherent, len(errors), (statistics.median(errors) if errors else None), latest_ok


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
        coherent, tested, median_error, latest_ok = _score(dev, undev, total)
        if tested == 0:
            continue
        tried += 1
        # A combination that holds in the latest period outranks one that
        # merely holds more often, because the latest period is the one the
        # panel shows. Then most coherent periods, then lowest error.
        rank = (
            latest_ok,
            coherent,
            -(median_error if median_error is not None else 1e9),
            tested,
        )
        candidate = FamilyValidation(
            cik=cik,
            status=(
                STATUS_VALIDATED if latest_ok
                else STATUS_DRIFTED if coherent
                else STATUS_INCOHERENT
            ),
            choices={dev_key: dev, undev_key: undev, total_key: total},
            tested_periods=tested,
            coherent_periods=coherent,
            median_error=median_error,
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
            note=(
                f"held for {result.coherent_periods} of {result.tested_periods} "
                f"periods but not the most recent; the tag's meaning appears to "
                f"have changed (median error {pct})"
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
            note=(
                f"no combination of {tried} satisfies developed + undeveloped = "
                f"total; best median error {pct}"
            ),
        )
    return result
