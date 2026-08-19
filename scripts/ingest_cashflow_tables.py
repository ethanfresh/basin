"""Ingest the standardized measure from the ASC 932 cash-flow note.

    python scripts/ingest_cashflow_tables.py --dry-run
    python scripts/ingest_cashflow_tables.py

XBRL carries the standardized measure for 47 of the 91 cohort members. The note
that states it is locatable in an annual report for 87 of them, and it is the
one panel column whose figure the filing checks for you: ASC 932-235-50-31 fixes
the line items, so the deductions must sum to future net cash flows and future
net cash flows less the 10% discount must be the measure itself. See
:mod:`basin.documents.cashflow`.

Nothing is fetched: the filings are already in the corpus.

Three gates stand between a parsed figure and a stored one.

**The table has to declare its magnitude.** This is dollars, not barrels, and
the scale is stated once in a caption -- "(in thousands)" -- with every figure
beneath silent about its own size. A table that declares none yields nothing,
because the alternative is a plausible wrong number by a factor of a thousand
in the column the scale resolver uses as its own referent.

**A checked reading beats an unchecked one.** A filing prints the measure in the
build-up table and again, bare, in a summary. Only the first can be verified
against its own arithmetic, so where both are present the checked one wins, and
two checked readings that disagree drop the cell rather than picking one.

**The XBRL has to agree, where there is XBRL.** The 47 filers that tag it are a
labelled test set the extractor did not get to see.
"""

from __future__ import annotations

import argparse
import collections
import math
from pathlib import Path

from basin.documents import corpus
from basin.documents.cashflow import MeasureReading, measure_readings
from basin.documents.sites import CASHFLOW, table_hits
from basin.facts.concepts import BY_KEY
from basin.facts.xbrl import FactRow
from basin.store import DEFAULT_DB_PATH, connect, insert_facts

CONCEPT = "standardized_measure"
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")

# Two readings of one cell agree when they round to the same figure. The
# measure is printed to the dollar in thousands, so this is generous.
TOLERANCE = 0.01


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--corpus", type=Path, default=corpus.CORPUS)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--cik", action="append")
    parser.add_argument("--limit", type=int)
    parser.add_argument("--form", action="append", default=[])
    return parser.parse_args(argv)


def _is_power_of_ten(ratio: float) -> bool:
    if ratio <= 0:
        return False
    exponent = math.log10(ratio)
    return abs(exponent - round(exponent)) < 1e-6 and round(exponent) != 0


def choose(readings: list[MeasureReading]) -> tuple[MeasureReading | None, str]:
    """The reading to store for one period, and why.

    A checked reading -- both identities closing -- outranks an unchecked one
    outright, however many unchecked ones there are. Agreement between two
    unverifiable figures is not evidence; it is usually the same figure printed
    twice.
    """
    if not readings:
        return None, "none"
    checked = [r for r in readings if r.checked]
    if checked:
        first = checked[0]
        if all(abs(r.value - first.value) / abs(first.value) <= TOLERANCE
               for r in checked if first.value):
            return first, "checked"
        return None, "checked readings disagree"
    first = readings[0]
    if all(abs(r.value - first.value) / abs(first.value) <= TOLERANCE
           for r in readings if first.value):
        return first, "unchecked, but the filing states it only once"
    return None, "unchecked readings disagree"


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)
    forms = tuple(args.form) or ANNUAL_FORMS

    located = table_hits(conn, CASHFLOW, forms=forms)
    if args.cik:
        wanted = set(args.cik)
        located = [h for h in located if h.cik in wanted]
    targets = [h for h in located if h.has_table]
    if args.limit:
        targets = targets[: args.limit]

    print(f"standardized-measure language in {len(located):,} documents; "
          f"{len(targets):,} hold a table, across "
          f"{len({h.cik for h in targets})} filers\n")

    tagged = {
        (r["cik"], r["period_end"]): r["value"]
        for r in conn.execute(
            "SELECT cik, period_end, value FROM fact WHERE concept_key = ? "
            "AND extracted_by LIKE 'xbrl%'",
            (CONCEPT,),
        )
    }

    counts: collections.Counter = collections.Counter()
    # (cik, period) -> readings from every document of the filer
    pool: dict[tuple[str, str], list[MeasureReading]] = collections.defaultdict(list)
    origin: dict[tuple[str, str], tuple[str, str, str]] = {}

    for n, site in enumerate(targets, 1):
        path = args.corpus / site.accession / site.name
        if not path.exists():
            counts["located but not on disk"] += 1
            continue
        readings = measure_readings(path.read_text(errors="replace"))
        if not readings:
            counts["located, but no table the parser could read"] += 1
            continue
        counts["documents with a readable note"] += 1
        for reading in readings:
            key = (site.cik, reading.period_end)
            pool[key].append(reading)
            # Keep the accession of the first document to state it; the same
            # figure is restated by later filings and the earliest citation is
            # the one that first published it.
            origin.setdefault(key, (site.accession, site.form, site.filed_date))
        if n % 100 == 0:
            print(f"  {n}/{len(targets)} documents", flush=True)

    agree = disagree = rescaled = 0
    mismatches: list[str] = []
    rows: list[FactRow] = []
    spec = BY_KEY.get(CONCEPT)

    for (cik, period), readings in sorted(pool.items()):
        chosen, why = choose(readings)
        counts[f"cell: {why}"] += 1
        if chosen is None:
            continue

        expected = tagged.get((cik, period))
        if expected:
            ratio = chosen.value / expected
            if abs(ratio - 1) <= TOLERANCE:
                agree += 1
            elif _is_power_of_ten(ratio):
                rescaled += 1
            else:
                disagree += 1
                if len(mismatches) < 20:
                    mismatches.append(
                        f"  {cik} {period}  table {chosen.value:>18,.0f} "
                        f"vs xbrl {expected:>18,.0f}  ({ratio:.4g}x)"
                    )

        accession, form, filed = origin[(cik, period)]
        rows.append(
            FactRow(
                cik=cik,
                concept_key=CONCEPT,
                taxonomy=None,
                tag=None,
                value=chosen.value,
                unit="USD",
                product=None,
                unit_rank=spec.unit_rank("USD") if spec else 0,
                period_start=None,
                period_end=period,
                fiscal_year=int(period[:4]),
                fiscal_period="FY",
                accession=accession,
                form=form,
                filed=filed,
                extracted_by="table:cashflow",
                source_span=chosen.source_span,
                section=chosen.row_label,
            )
        )

    counts["rows"] = len(rows)
    counts["filers"] = len({r.cik for r in rows})
    print()
    for key, value in counts.most_common():
        print(f"  {key:<52}{value:>7,}")
    if agree or disagree or rescaled:
        total = agree + disagree + rescaled
        print(f"\n  cross-check against tagged filers: {agree:,} agree, "
              f"{rescaled:,} agree on the digits but not the declared magnitude, "
              f"{disagree:,} disagree ({(agree + rescaled) / total:.1%} on value)")
        for line in mismatches:
            print(line)

    if args.dry_run:
        print(f"\ndry run; {len(rows):,} rows not written")
        return 0

    written = insert_facts(conn, rows)
    conn.commit()
    print(f"\n  rows written: {written:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
