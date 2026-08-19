"""Ingest reserve quantities from the reserve tables in stored annual reports.

    python scripts/ingest_reserve_tables.py --dry-run
    python scripts/ingest_reserve_tables.py

The XBRL path reaches 41 of the 94 cached filers for proved developed reserves
— not because the other 53 withhold the number, but because the reserve
quantity table sits outside the financial statements and therefore outside the
detail-tagging requirement. See :mod:`basin.documents.reserves`. This reads
the table.

Which documents to read comes from the full-text index rather than from a
guess. :mod:`basin.documents.sites` asks ``document_search`` which documents
use reserve-table language and where the rows cluster, which fixes the two
things guessing got wrong: it reads every document of a filing rather than only
the primary one -- a 40-F is usually a cover sheet whose reserve statements are
in an attached Annual Information Form -- and it skips documents with no reserve
table instead of parsing a few hundred tables to find out. On a 60-document
sample it flagged every document the extractor could read, and ruled out 27 of
60 outright.

That also removes the ``10-K`` restriction, which silently excluded every
foreign private issuer in the cohort. They file 20-F and 40-F and never a 10-K.

Nothing is fetched: the filings are already in the corpus.

Two gates stand between a parsed figure and a stored one.

**The rollforward has to close.** Proved developed plus proved undeveloped
equals total proved, per product, in every filer's table, because that is what
the categories mean. A period where it does not close is a period the parse
got wrong, and it is dropped whole rather than partly — a plausible half of a
misread table is worse than a gap.

**The XBRL has to agree, where there is XBRL.** 41 filers tag the figure and
also print it, which is a labelled test set the extractor did not get to see.
``--dry-run`` scores against it and writes nothing; the score is the argument
for trusting the other 53.
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.documents import corpus
from basin.documents.reserves import ReserveReading, reserve_readings
from basin.documents.sites import reserve_hits
from basin.facts.concepts import BY_KEY
from basin.facts.units import conversion_for
from basin.facts.xbrl import FactRow
from basin.store import DEFAULT_DB_PATH, connect, insert_facts

CONCEPTS = (
    "proved_developed_reserves_boe",
    "proved_undeveloped_reserves_boe",
    "proved_reserves_boe",
)

# How far apart two figures may sit and still be the same figure. Filers print
# reserves to one decimal place in MMBoe, so the developed/undeveloped split
# can miss the total by a rounding step per component; and a filer's own
# rollforward is allowed to be off by the same. Wider than this is a parse
# error, not rounding.
TOLERANCE = 0.006

# The annual reports that carry reserve disclosure. 20-F and 40-F are here
# because the previous 10-K-only selection excluded all 19 IFRS-reporting
# cohort members, which is the population whose reserves are least likely to be
# in XBRL and therefore most in need of being read off the page.
ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--corpus", type=Path, default=corpus.CORPUS)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="score against tagged filers and write nothing",
    )
    parser.add_argument("--cik", action="append", help="limit to these CIKs")
    parser.add_argument("--limit", type=int, help="stop after N documents")
    parser.add_argument(
        "--form", action="append", default=[],
        help=f"annual forms to read (default: {' '.join(ANNUAL_FORMS)})",
    )
    return parser.parse_args(argv)


def _boe(value: float, unit: str) -> float | None:
    """A reading in BOE, for comparing figures that are in different units.

    Used only for the arithmetic gate and the XBRL cross-check. The stored row
    keeps the filing's own unit and lets the scale resolver do its job.
    """
    conversion = conversion_for(unit)
    return None if conversion is None else value * conversion.factor


def closes(readings: list[ReserveReading]) -> tuple[set[str], dict[str, str]]:
    """Periods whose developed + undeveloped equals total proved.

    Returns the periods that close and, for the rest, why not — a period with
    only one of the three categories cannot be checked and is reported as
    unchecked rather than passed.
    """
    by_period: dict[str, dict[tuple[str, str | None], float]] = (
        collections.defaultdict(dict)
    )
    for reading in readings:
        boe = _boe(reading.value, reading.unit)
        if boe is None:
            continue
        key = (reading.concept_key, reading.product)
        # The same figure appears in Item 2 and in the reserve note. They agree;
        # keeping the first is enough, and disagreement is caught by the sum.
        by_period[reading.period_end].setdefault(key, boe)

    good: set[str] = set()
    reasons: dict[str, str] = {}
    for period, cells in by_period.items():
        products = {product for _, product in cells}
        checked = failed = paired = 0
        for product in products:
            developed = cells.get(("proved_developed_reserves_boe", product))
            undeveloped = cells.get(("proved_undeveloped_reserves_boe", product))
            total = cells.get(("proved_reserves_boe", product))
            if developed is not None and undeveloped is not None:
                paired += 1
            if developed is None or undeveloped is None or total is None:
                continue
            checked += 1
            if total and abs(developed + undeveloped - total) / total > TOLERANCE:
                failed += 1
        if failed:
            reasons[period] = f"{failed}/{checked} products do not close"
        elif checked:
            good.add(period)
        elif paired:
            # Both halves of the split, but no total printed in any table of
            # this filing to add them against — Barnwell prints one table per
            # product with developed and undeveloped balances and no proved
            # total anywhere. The parse is still constrained: the two figures
            # come from the same columns of the same table, so a misread axis
            # would have to be wrong identically in both. Weaker than the
            # identity, and counted separately so the mix stays visible.
            good.add(period)
            reasons[period] = "kept on the developed/undeveloped pair only"
        else:
            reasons[period] = "only one category present, nothing to check against"
    return good, reasons


def _is_power_of_ten(ratio: float) -> bool:
    """Whether two figures differ only by a factor of 10, 100, 1000 ..."""
    import math

    if ratio <= 0:
        return False
    exponent = math.log10(ratio)
    return abs(exponent - round(exponent)) < 1e-6 and round(exponent) != 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)

    forms = tuple(args.form) or ANNUAL_FORMS
    period_of = {
        r["accession"]: r["period_end"]
        for r in conn.execute("SELECT accession, period_end FROM filing")
    }

    # Ask the index where the reserve tables are, rather than opening the
    # primary document of every annual filing and parsing every table in it.
    # This is what reaches an exhibit: a 40-F's reserve statements are in the
    # attached Annual Information Form, not in the cover sheet EDGAR calls the
    # primary document.
    located = reserve_hits(conn, forms=forms)
    if args.cik:
        wanted = set(args.cik)
        located = [h for h in located if h.cik in wanted]

    # Reported, not dropped. A document that discusses reserves and holds no
    # table row is the wrapper-form signature, and the count is a coverage fact
    # about the corpus -- the tables are somewhere the fetcher has not reached.
    prose_only = [h for h in located if not h.has_table]
    targets = [h for h in located if h.has_table]
    if args.limit:
        targets = targets[: args.limit]

    print(f"reserve language in {len(located):,} documents of {len(forms)} annual "
          f"form(s): {len(targets):,} hold a table, {len(prose_only):,} prose only")
    closable = sum(1 for h in targets if h.best and h.best.closable)
    print(f"  {closable:,} name developed, undeveloped and total -- the identity "
          f"check can run on those\n")

    # The labelled test set: what XBRL already says, keyed the way a stored
    # cell is keyed. Only tagged rows — comparing against our own output would
    # prove nothing.
    tagged = {
        (r["cik"], r["concept_key"], r["product"], r["period_end"]): (
            r["value"],
            r["unit"],
        )
        for r in conn.execute(
            "SELECT cik, concept_key, product, period_end, value, unit FROM fact "
            f"WHERE concept_key IN ({','.join('?' * len(CONCEPTS))}) "
            "AND extracted_by LIKE 'xbrl%'",
            CONCEPTS,
        )
    }

    counts: collections.Counter = collections.Counter()
    agree = disagree = rescaled = 0
    mismatches: list[str] = []
    written_total = 0

    for n, site in enumerate(targets, 1):
        path = args.corpus / site.accession / site.name
        if not path.exists():
            counts["located but not on disk"] += 1
            continue
        meta = {
            "accession": site.accession,
            "cik": site.cik,
            "form": site.form,
            "filed_date": site.filed_date,
            "period_end": period_of.get(site.accession),
        }
        raw = path.read_text(errors="replace")
        readings = reserve_readings(raw, fallback_period=meta.get("period_end"))
        if not readings:
            # The locator found reserve rows in this document, so this is a
            # parser gap rather than a filer that discloses nothing. Naming it
            # that way is the point of having a locator at all.
            counts["located, but no table the parser could read"] += 1
            continue

        good, reasons = closes(readings)
        for period, reason in reasons.items():
            prefix = "period kept" if period in good else "period dropped"
            counts[f"{prefix}: {reason}"] += 1
        counts["period kept: identity closes"] += len(good) - sum(
            1 for period in good if period in reasons
        )
        readings = [r for r in readings if r.period_end in good]
        if not readings:
            counts["nothing survived the arithmetic check"] += 1
            continue
        counts["documents with a readable reserve table"] += 1

        # Two readings for one cell that do not agree mean the parse is wrong
        # somewhere, and there is no basis for preferring either. The cell is
        # dropped rather than resolved by insertion order.
        seen: dict[tuple, float] = {}
        conflicted: set[tuple] = set()
        for reading in readings:
            key = (
                meta["cik"],
                reading.concept_key,
                reading.product,
                reading.period_end,
                reading.unit,
            )
            if key in seen and abs(seen[key] - reading.value) > 1e-6:
                conflicted.add(key)
            seen.setdefault(key, reading.value)
        counts["cells dropped: two tables disagree"] += len(conflicted)

        rows: dict[tuple, FactRow] = {}
        for reading in readings:
            key = (
                meta["cik"],
                reading.concept_key,
                reading.product,
                reading.period_end,
                reading.unit,
            )
            if key in rows or key in conflicted:
                continue

            expected = tagged.get(key[:4])
            if expected is not None:
                mine = _boe(reading.value, reading.unit)
                theirs = _boe(expected[0], expected[1])
                if mine and theirs:
                    if abs(mine - theirs) / theirs <= TOLERANCE:
                        agree += 1
                    elif _is_power_of_ten(mine / theirs):
                        # Same digits, different declared magnitude. Talos tags
                        # 85,007 MMBbls where its own table column reads
                        # (MBbls); Range tags 21,290 MMBbls against (MBbls) in
                        # the same row. The table cannot make this error -- the
                        # figure is as printed and its unit is its column
                        # header -- so it is the table being right, not a
                        # disagreement about what the number is.
                        rescaled += 1
                    else:
                        disagree += 1
                        if len(mismatches) < 25:
                            mismatches.append(
                                f"  {meta['cik']} {reading.concept_key[:26]:26} "
                                f"{str(reading.product):5} {reading.period_end}  "
                                f"table {reading.value:>12,.1f} {reading.unit:7} "
                                f"vs xbrl {expected[0]:>14,.1f} {expected[1]:7} "
                                f"({mine / theirs:.4g}x)"
                            )

            spec = BY_KEY.get(reading.concept_key)
            rows[key] = FactRow(
                cik=meta["cik"],
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
                form=meta["form"],
                filed=meta["filed_date"],
                # Named for the mechanism, not the concept: a reader deciding
                # whether to trust a cell needs to know it was read off a table
                # rather than identified by a tag.
                extracted_by="table:reserves",
                source_span=reading.source_span,
                section=reading.column_label,
            )

        if not args.dry_run:
            written_total += insert_facts(conn, list(rows.values()))
        counts["rows"] += len(rows)

        if n % 200 == 0:
            if not args.dry_run:
                conn.commit()
            print(f"  {n}/{len(targets)} documents, {counts['rows']:,} rows",
                      flush=True)

    if not args.dry_run:
        conn.commit()

    print()
    for key, value in counts.most_common():
        print(f"  {key:<48}{value:>7,}")
    if agree or disagree or rescaled:
        total = agree + disagree + rescaled
        print(
            f"\n  cross-check against tagged filers: {agree:,} agree, "
            f"{rescaled:,} agree on the digits but not the declared magnitude, "
            f"{disagree:,} disagree ({(agree + rescaled) / total:.1%} on value)"
        )
        for line in mismatches:
            print(line)
    if not args.dry_run:
        print(f"\n  rows written: {written_total:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
