"""Read typed fact rows out of the SEC's XBRL ``companyfacts`` API.

Nothing in this module involves a language model. A value here was tagged by
the filer, and it arrives with the accession number of the filing that carried
it — which is what makes the Facts layer evaluable by exact match.

Endpoints used:

    https://data.sec.gov/api/xbrl/companyfacts/CIK##########.json
    https://data.sec.gov/api/xbrl/companyconcept/CIK##########/{taxonomy}/{tag}.json

``companyfacts`` returns every tagged fact for a filer in one document, so it
is one request per company rather than one per concept. ``companyconcept`` is
kept for spot-checking a single field without refetching the whole payload.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Iterable, Iterator

from basin.edgar.client import SEC_DATA_HOST, EdgarClient, NotFound, cik_padded
from basin.facts.concepts import ALL_CONCEPTS, ConceptSpec


@dataclass(frozen=True)
class FactRow:
    """One tagged value, with everything needed to cite it.

    The field set mirrors the fact store's columns deliberately: a row that
    cannot be written to the store should not exist in memory either.
    """

    cik: str
    concept_key: str
    """Basin's stable name for the field."""

    taxonomy: str
    tag: str
    """The alias that actually carried the value for this filer."""

    value: float
    unit: str

    period_start: str | None
    period_end: str
    fiscal_year: int | None
    fiscal_period: str | None

    accession: str
    form: str
    filed: str

    frame: str | None = None
    """SEC's calendar-aligned frame key, when it assigned one."""

    extracted_by: str = "xbrl"

    @property
    def is_duration(self) -> bool:
        """True for flow facts (production, capex); False for point-in-time."""
        return self.period_start is not None


def companyfacts_url(cik: int | str) -> str:
    return f"{SEC_DATA_HOST}/api/xbrl/companyfacts/CIK{cik_padded(cik)}.json"


def companyconcept_url(cik: int | str, taxonomy: str, tag: str) -> str:
    return (
        f"{SEC_DATA_HOST}/api/xbrl/companyconcept/"
        f"CIK{cik_padded(cik)}/{taxonomy}/{tag}.json"
    )


def fetch_companyfacts(client: EdgarClient, cik: int | str) -> dict[str, Any]:
    """Fetch the full companyfacts payload for one filer.

    Propagates :class:`~basin.edgar.client.NotFound` — a filer with no XBRL at
    all is a real and reportable state, not an error to swallow.
    """
    return client.get_json(companyfacts_url(cik))


def resolve_alias(
    payload: dict[str, Any], concept: ConceptSpec
) -> tuple[str, str] | None:
    """Return the first ``(taxonomy, tag)`` alias this filer actually tagged.

    Order matters: aliases are listed most-reliable-first, so the winner is the
    one Basin prefers, not merely one that exists.
    """
    facts = payload.get("facts", {})
    for taxonomy, tag in concept.aliases:
        if tag in facts.get(taxonomy, {}):
            return taxonomy, tag
    return None


def rows_for_concept(
    payload: dict[str, Any],
    concept: ConceptSpec,
    *,
    forms: Iterable[str] | None = ("10-K", "10-K/A"),
) -> list[FactRow]:
    """Extract every fact row for one concept from a companyfacts payload.

    ``forms`` filters by originating form; pass ``None`` to keep all of them.
    Amendments are kept alongside originals rather than replacing them — the
    store is append-only, and a citation must still resolve after a restatement.
    """
    resolved = resolve_alias(payload, concept)
    if resolved is None:
        return []

    taxonomy, tag = resolved
    cik = cik_padded(payload["cik"])
    entry = payload["facts"][taxonomy][tag]
    wanted_forms = set(forms) if forms is not None else None

    rows: list[FactRow] = []
    for unit, observations in entry.get("units", {}).items():
        for obs in observations:
            form = obs.get("form", "")
            if wanted_forms is not None and form not in wanted_forms:
                continue
            # 'end' and 'accn' are what make a row citable. A fact missing
            # either cannot be cited, so it is not a fact Basin will store.
            if "end" not in obs or "accn" not in obs or "val" not in obs:
                continue
            rows.append(
                FactRow(
                    cik=cik,
                    concept_key=concept.key,
                    taxonomy=taxonomy,
                    tag=tag,
                    value=float(obs["val"]),
                    unit=unit,
                    period_start=obs.get("start"),
                    period_end=obs["end"],
                    fiscal_year=obs.get("fy"),
                    fiscal_period=obs.get("fp"),
                    accession=obs["accn"],
                    form=form,
                    filed=obs.get("filed", ""),
                    frame=obs.get("frame"),
                )
            )

    rows.sort(key=lambda r: (r.period_end, r.filed, r.accession))
    return rows


def rows_for_all_concepts(
    payload: dict[str, Any],
    concepts: Iterable[ConceptSpec] = ALL_CONCEPTS,
    *,
    forms: Iterable[str] | None = ("10-K", "10-K/A"),
) -> Iterator[FactRow]:
    """Yield fact rows for every concept in the registry."""
    for concept in concepts:
        yield from rows_for_concept(payload, concept, forms=forms)


@dataclass(frozen=True)
class ConceptCoverage:
    """Whether one filer tagged one concept, and under which alias."""

    concept_key: str
    tagged: bool
    taxonomy: str | None
    tag: str | None
    observation_count: int
    units: tuple[str, ...]
    latest_period_end: str | None


@dataclass(frozen=True)
class CompanyCoverage:
    """Per-company coverage across the whole concept registry."""

    cik: str
    entity_name: str
    concepts: tuple[ConceptCoverage, ...]
    error: str | None = None

    @property
    def tagged_count(self) -> int:
        return sum(1 for c in self.concepts if c.tagged)


def coverage_for_company(
    client: EdgarClient,
    cik: int | str,
    concepts: Iterable[ConceptSpec] = ALL_CONCEPTS,
    *,
    forms: Iterable[str] | None = ("10-K", "10-K/A"),
) -> CompanyCoverage:
    """Measure which registry concepts a filer actually tags.

    A filer with no XBRL facts is reported as fully-untagged with an ``error``
    note rather than raising — the point of a coverage report is to count the
    gaps, and a filer that is entirely absent is the largest gap there is.
    """
    concepts = tuple(concepts)
    try:
        payload = fetch_companyfacts(client, cik)
    except NotFound:
        return CompanyCoverage(
            cik=cik_padded(cik),
            entity_name="",
            concepts=tuple(
                ConceptCoverage(c.key, False, None, None, 0, (), None)
                for c in concepts
            ),
            error="no companyfacts payload (404)",
        )

    results: list[ConceptCoverage] = []
    for concept in concepts:
        rows = rows_for_concept(payload, concept, forms=forms)
        resolved = resolve_alias(payload, concept)
        results.append(
            ConceptCoverage(
                concept_key=concept.key,
                tagged=bool(rows),
                taxonomy=resolved[0] if resolved else None,
                tag=resolved[1] if resolved else None,
                observation_count=len(rows),
                units=tuple(sorted({r.unit for r in rows})),
                latest_period_end=rows[-1].period_end if rows else None,
            )
        )

    return CompanyCoverage(
        cik=cik_padded(payload["cik"]),
        entity_name=payload.get("entityName", ""),
        concepts=tuple(results),
    )
