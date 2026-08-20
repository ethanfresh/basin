"""Ingest production, realized price and production cost from the S-K 1204 table.

    python scripts/ingest_production_tables.py --dry-run
    python scripts/ingest_production_tables.py

Three panel columns come from one required disclosure, and XBRL reaches almost
none of it: production volume 41% of the cohort, average sales price 9%,
production cost per unit 2%. Regulation S-K Item 1204 requires all three of
every producer, for each of the last three years, by product. The number is
public; it is simply not tagged. See :mod:`basin.documents.production`.

Which documents to read comes from the full-text index
(:mod:`basin.documents.sites`), which finds the table in 520 documents across
68 of the 91 cohort members.

Nothing is fetched: the filings are already in the corpus.

Three gates stand between a parsed figure and a stored one.

**The BOE identity has to close.** Oil plus NGL plus gas at 6 Mcf per barrel
equals the printed equivalent total, because that is what the total means. A
period where it does not close is a period whose column axis or units were
misread, and it is dropped whole.

**The table has to be the consolidated one.** A filing prints this table once
for the company and again per segment or per field, and every one of them
closes the BOE identity — Talos's Gulf of Mexico table is as internally
consistent as its consolidated table. What separates them is agreement with a
consolidated figure reported elsewhere: total volume times realized price
against oil and gas revenue, which XBRL carries for 71 filers. Where revenue is
unavailable, the largest-volume table is not assumed to be right; the cell is
dropped unless every candidate table agrees.

**The XBRL has to agree, where there is XBRL.** The filers that do tag these
concepts are a labelled test set the extractor did not get to see. ``--dry-run``
scores against it and writes nothing.

One price per cell is stored, and where a filer prints both bases it is the
unhedged one. Realized price including derivative settlements is a fact about
the filer's hedge book as much as about its barrels, and a panel that puts one
company's hedged price beside another's wellhead price compares two different
quantities. The basis is recorded in ``fact.is_hedged`` either way, so a reader
can see which they are looking at.
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.documents import corpus
from basin.documents.production import (
    COST,
    PRICE,
    VOLUME,
    VOLUME_TOLERANCE,
    ProductionReading,
    implied_revenue,
    production_readings,
    readings_for_table,
    to_boe,
    volumes_close,
)
from basin.documents.production import _SECTIONS as SECTION_PATTERNS
from basin.documents.sites import PRODUCTION, table_hits
from basin.documents.tables import parse_tables
from basin.facts.concepts import BY_KEY
from basin.facts.xbrl import FactRow
from basin.store import DEFAULT_DB_PATH, connect, insert_facts

CONCEPTS = (VOLUME, PRICE, COST)

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")

# How close two readings of the same cell must be to count as the same figure.
TOLERANCE = 0.01

# See basin.documents.production.REVENUE_TOLERANCE.
from basin.documents.production import REVENUE_TOLERANCE  # noqa: E402


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--corpus", type=Path, default=corpus.CORPUS)
    parser.add_argument("--dry-run", action="store_true",
                        help="score against tagged filers and write nothing")
    parser.add_argument("--cik", action="append", help="limit to these CIKs")
    parser.add_argument("--limit", type=int, help="stop after N documents")
    parser.add_argument("--form", action="append", default=[])
    parser.add_argument(
        "--replace", action="store_true",
        help="delete this extractor's previous rows before writing. Use when "
             "the extractor itself has changed: inserts conflict-and-skip, so "
             "a re-run cannot correct a row it wrote under older rules",
    )
    return parser.parse_args(argv)


def tables_in(raw: str) -> list[list[ProductionReading]]:
    """Readings grouped by the table they came from, tables with none dropped."""
    out = []
    for table in parse_tables(raw):
        if not any(p.search(c.text) for c in table.cells for _, p in SECTION_PATTERNS):
            continue
        readings = readings_for_table(table, raw)
        if readings:
            out.append(readings)
    return out


def total_volume(readings: list[ProductionReading], period_end: str) -> float | None:
    """The table's own equivalent-total production for one period, in BOE."""
    for r in readings:
        if r.concept_key == VOLUME and r.product is None and r.period_end == period_end:
            return to_boe(r.value, r.unit)
    return None


def _best_by(
    candidates: list[list[ProductionReading]],
    reported: dict[str, float],
    implied,
    tolerance: float,
) -> tuple[list[ProductionReading], float] | None:
    """The candidate whose implied figure sits closest to *reported*."""
    scored: list[tuple[float, list[ProductionReading]]] = []
    for readings in candidates:
        errors = [
            abs(value - reported[period]) / reported[period]
            for period in reported
            if reported[period] and (value := implied(readings, period)) is not None
        ]
        if errors:
            scored.append((min(errors), readings))
    if not scored:
        return None
    scored.sort(key=lambda s: s[0])
    error, best = scored[0]
    return (best, error) if error <= tolerance else ([], error)


def choose_table(
    candidates: list[list[ProductionReading]],
    revenue: dict[str, float],
    volume: dict[str, float] | None = None,
) -> tuple[list[ProductionReading], str]:
    """The consolidated table among several, and why it was chosen.

    Returns an empty list when the evidence does not settle it. A segment table
    stored as the company's production is a wrong number, and a wrong number
    costs more than a missing one.
    """
    if not candidates:
        return [], "no table"
    if len(candidates) == 1:
        return candidates[0], "only one table"

    by_revenue = _best_by(candidates, revenue, implied_revenue, REVENUE_TOLERANCE)
    if by_revenue and by_revenue[0]:
        return by_revenue[0], f"reconciles to reported revenue ({by_revenue[1]:.1%})"

    # Volume is the weaker test -- it constrains the volume rows and says
    # nothing about the price ones -- so it is only consulted where revenue
    # could not decide, and it is held to a tighter tolerance because a
    # production volume is reported exactly rather than averaged over a year.
    by_volume = _best_by(candidates, volume or {}, total_volume, VOLUME_TOLERANCE)
    if by_volume and by_volume[0]:
        return by_volume[0], f"reconciles to reported production ({by_volume[1]:.1%})"

    if by_revenue:
        return [], f"no table reconciles to revenue (best {by_revenue[1]:.1%})"
    if by_volume:
        return [], f"no table reconciles to production (best {by_volume[1]:.1%})"

    # No revenue to check against. Accept only if every table agrees, which
    # happens when a filing simply repeats the same table.
    merged = candidates[0]
    for other in candidates[1:]:
        if _cells(other) != _cells(merged):
            return [], "several tables disagree and no revenue to choose between them"
    return merged, "identical tables"


SOURCE = "table:production"


def _replace_previous(conn) -> int:
    """Delete every row this extractor wrote, and what depends on them.

    The fact store is append-only and inserts conflict-and-skip, which is right
    for ingesting the same filing twice and wrong for an extractor whose rules
    have changed: the row written under the old rules stays, and the corrected
    one cannot replace it. A cost of $0.06 per Mcfe stored as $0.06 per BOE
    does not go away by re-running.

    Safe because these rows are derived: every one is reproducible from a
    document already in the corpus, which is not true of anything else here.
    """
    ids = [r[0] for r in conn.execute(
        "SELECT id FROM fact WHERE extracted_by = ?", (SOURCE,)
    )]
    with conn:
        for table in ("fact_verification", "fact_scale", "vision_check"):
            conn.executemany(
                f"DELETE FROM {table} WHERE fact_id = ?", [(i,) for i in ids]
            )
        conn.executemany("DELETE FROM fact WHERE id = ?", [(i,) for i in ids])
    return len(ids)


def _is_power_of_ten(ratio: float) -> bool:
    """Whether two figures differ only by a factor of 10, 100, 1000 ..."""
    import math

    if ratio <= 0:
        return False
    exponent = math.log10(ratio)
    return abs(exponent - round(exponent)) < 1e-6 and round(exponent) != 0


def _unambiguous_costs(
    candidates: list[list[ProductionReading]]
) -> tuple[list[ProductionReading], str]:
    """Cost readings that no scope question hangs over.

    Cost is safe to keep when the document states it once: either one table
    carries cost rows, or several do and they agree cell for cell, which is a
    filing repeating itself. Where they disagree the scope question is real
    after all -- one of them is a field or a segment -- and nothing is kept.
    """
    with_costs = [
        [r for r in readings if r.concept_key == COST]
        for readings in candidates
    ]
    with_costs = [c for c in with_costs if c]
    if not with_costs:
        return [], "no cost rows"
    if len(with_costs) == 1:
        return with_costs[0], "only one table states a cost"

    # Two tables covering different years are not in conflict -- ExxonMobil
    # prints one block per year, and requiring identical cell sets rejected the
    # whole filing because 2024's table says nothing about 2025. Only cells
    # both tables actually state can disagree.
    merged: dict[tuple, ProductionReading] = {}
    for readings in with_costs:
        for r in readings:
            key = (r.product, r.period_end, r.unit)
            seen = merged.get(key)
            if seen is None:
                merged[key] = r
            elif abs(seen.value - r.value) > 1e-6:
                return [], "tables state different costs for the same period"
    return list(merged.values()), "several tables, no overlap in disagreement"


def _prefer_unhedged(readings: list[ProductionReading]) -> list[ProductionReading]:
    """Keep one realized price per cell, the wellhead basis where both exist."""
    best: dict[tuple, ProductionReading] = {}
    passthrough: list[ProductionReading] = []
    for r in readings:
        if r.concept_key != PRICE:
            passthrough.append(r)
            continue
        key = (r.product, r.period_end, r.unit)
        current = best.get(key)
        if current is None or (current.is_hedged is not False and r.is_hedged is False):
            best[key] = r
    return passthrough + list(best.values())


def _cells(readings: list[ProductionReading]) -> dict:
    return {
        (r.concept_key, r.product, r.period_end, r.is_hedged): round(r.value, 4)
        for r in readings
    }


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)
    forms = tuple(args.form) or ANNUAL_FORMS

    located = table_hits(conn, PRODUCTION, forms=forms)
    if args.cik:
        wanted = set(args.cik)
        located = [h for h in located if h.cik in wanted]
    targets = [h for h in located if h.has_table]
    if args.limit:
        targets = targets[: args.limit]

    print(f"S-K 1204 language in {len(located):,} documents; {len(targets):,} hold a "
          f"table, across {len({h.cik for h in targets})} filers\n")

    # Consolidated revenue per filer and period, for the table-selection gate.
    revenue: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for r in conn.execute(
        "SELECT cik, period_end, value FROM fact WHERE concept_key = "
        "'oil_and_gas_revenue' AND extracted_by LIKE 'xbrl%'"
    ):
        revenue[r["cik"]].setdefault(r["period_end"], r["value"])

    # The second referent, for filers whose revenue is untagged. Only the
    # equivalent-total rows: a per-product volume cannot be compared against a
    # table's BOE total without the conversion this is meant to check.
    volume: dict[str, dict[str, float]] = collections.defaultdict(dict)
    for r in conn.execute(
        "SELECT cik, period_end, value, unit FROM fact WHERE concept_key = "
        "'production_volume' AND product IS NULL AND extracted_by LIKE 'xbrl%'"
    ):
        boe = to_boe(r["value"], r["unit"])
        if boe:
            volume[r["cik"]].setdefault(r["period_end"], boe)

    # The labelled test set: the filers that do tag these concepts.
    tagged = {
        (r["cik"], r["concept_key"], r["product"], r["period_end"]): (r["value"], r["unit"])
        for r in conn.execute(
            "SELECT cik, concept_key, product, period_end, value, unit FROM fact "
            f"WHERE concept_key IN ({','.join('?' * len(CONCEPTS))}) "
            "AND extracted_by LIKE 'xbrl%'",
            CONCEPTS,
        )
    }

    if args.replace and not args.dry_run:
        removed = _replace_previous(conn)
        print(f"replacing {removed:,} rows written by a previous run\n")

    counts: collections.Counter = collections.Counter()
    agree = disagree = rescaled = 0
    mismatches: list[str] = []
    written_total = 0

    for n, site in enumerate(targets, 1):
        path = args.corpus / site.accession / site.name
        if not path.exists():
            counts["located but not on disk"] += 1
            continue

        candidates = tables_in(path.read_text(errors="replace"))
        if not candidates:
            counts["located, but no table the parser could read"] += 1
            continue

        readings, why = choose_table(
            candidates,
            revenue.get(site.cik or "", {}),
            volume.get(site.cik or "", {}),
        )
        counts[f"table choice: {why.split(' (')[0]}"] += 1

        # Table choice settles which table is the *company's* production, and
        # that question is about volume and price: a filing prints those per
        # segment as well as consolidated, and the tables are indistinguishable
        # from inside. A per-unit cost table is not part of that ambiguity --
        # it states a rate, not a scope, and most filings carry exactly one.
        # Dropping the whole document when the volume tables cannot be told
        # apart discarded cost rows that were never in question, for twelve
        # filers including CNX, Gulfport, Chord, Matador and SM.
        costs, cost_why = _unambiguous_costs(candidates)
        if costs and not any(r.concept_key == COST for r in readings):
            counts[f"cost kept separately: {cost_why}"] += 1
            readings = readings + costs
        if not readings:
            continue

        closes = volumes_close(readings)
        bad = {p for p, ok in closes.items() if not ok}
        counts["periods dropped: BOE identity does not close"] += len(bad)
        readings = [r for r in readings if r.period_end not in bad]
        if not readings:
            counts["nothing survived the identity check"] += 1
            continue
        counts["documents with a readable production table"] += 1

        # One price per cell, unhedged preferred. The fact identity index does
        # not carry the hedging basis, so writing both would silently drop
        # whichever arrived second -- and which that is would depend on the
        # order the filer happened to print them in.
        readings = _prefer_unhedged(readings)

        rows: dict[tuple, FactRow] = {}
        for reading in readings:
            key = (site.cik, reading.concept_key, reading.product,
                   reading.period_end, reading.unit)
            if key in rows:
                continue

            expected = tagged.get(key[:4])
            if expected is not None and expected[1] == reading.unit:
                mine, theirs = reading.value, expected[0]
                if theirs and abs(mine - theirs) / abs(theirs) <= TOLERANCE:
                    agree += 1
                elif theirs and _is_power_of_ten(mine / theirs):
                    # Same digits, different declared magnitude: the filer
                    # tagged a raw barrel count and labelled the unit MBbls.
                    # The table cannot make this error -- the figure is the
                    # figure as printed, and its unit is its column header --
                    # so this counts as the table being right, not as a
                    # disagreement about what the number is.
                    rescaled += 1
                else:
                    disagree += 1
                    if len(mismatches) < 20:
                        mismatches.append(
                            f"  {site.cik} {reading.concept_key[:24]:24} "
                            f"{str(reading.product):5} {reading.period_end}  "
                            f"table {mine:>12,.2f} vs xbrl {theirs:>12,.2f} "
                            f"{reading.unit}"
                        )

            spec = BY_KEY.get(reading.concept_key)
            rows[key] = FactRow(
                cik=site.cik,
                concept_key=reading.concept_key,
                taxonomy=None,
                tag=None,
                value=reading.value,
                unit=reading.unit,
                product=reading.product,
                unit_rank=spec.unit_rank(reading.unit) if spec else 0,
                period_start=None,
                period_end=reading.period_end,
                fiscal_year=int(reading.period_end[:4]),
                fiscal_period="FY",
                accession=site.accession,
                form=site.form,
                filed=site.filed_date,
                # Named for the mechanism: a reader deciding whether to trust a
                # cell needs to know it was read off a table, not tagged.
                extracted_by=SOURCE,
                source_span=reading.source_span,
                section=reading.section_label or reading.row_label,
                is_hedged=reading.is_hedged,
            )

        if not args.dry_run:
            written_total += insert_facts(conn, list(rows.values()))
        counts["rows"] += len(rows)
        for row in rows.values():
            counts[f"rows: {row.concept_key}"] += 1

        if n % 100 == 0:
            if not args.dry_run:
                conn.commit()
            print(f"  {n}/{len(targets)} documents, {counts['rows']:,} rows", flush=True)

    if not args.dry_run:
        conn.commit()

    print()
    for key, value in counts.most_common():
        print(f"  {key:<56}{value:>7,}")
    if agree or disagree or rescaled:
        total = agree + disagree + rescaled
        print(f"\n  cross-check against tagged filers: {agree:,} agree, "
              f"{rescaled:,} agree on the digits but not the declared magnitude, "
              f"{disagree:,} disagree ({(agree + rescaled) / total:.1%} on value)")
        for line in mismatches:
            print(line)
    if not args.dry_run:
        print(f"\n  rows written: {written_total:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
