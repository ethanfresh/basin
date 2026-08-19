"""Remove table-read rows filed against a period the filer never reported.

    python scripts/prune_stale_periods.py            # report only
    python scripts/prune_stale_periods.py --apply

The table extractors read a period from the column header, and until the
fiscal-year fix they assumed every year ends on 31 December. Eight cohort
filers close on another month -- Barnwell and National Fuel Gas in September,
Mexco in March, Evolution and Tamboran in June, Trio in October -- so their
figures were written against a date they never reported. Re-running the
extractors writes the correct rows but cannot remove the wrong ones: the fact
store is append-only and inserts conflict-and-skip by design.

The test is not "does this look like a December default". It is whether the
filer filed an annual report for that period at all. ``filing.period_end`` now
answers that for every filing, so a table row whose period matches no annual
filing of that filer, where the same figure exists at a period that does, is a
duplicate of a real cell at an invented date.

Both halves of that condition matter. Without the first, a legitimate period
the corpus happens not to cover would be deleted. Without the second, a filer
whose annual filings are missing from `filing` would lose real data. Rows that
fail only one are reported and kept.

Only ``table:`` rows are considered. XBRL rows carry their period from the
filing's own context and were never subject to this.

Three tables reference ``fact(id)`` -- the verification result, the resolved
scale, and the vision cross-check -- and all three are statements *about* a
row rather than facts in their own right. They are deleted with it. The
foreign keys exist so that cannot happen by accident, which is why it is done
explicitly here rather than by loosening them.
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.store import DEFAULT_DB_PATH, connect

ANNUAL_FORMS = ("10-K", "10-K/A", "20-F", "20-F/A", "40-F", "40-F/A")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.db)

    reported: dict[str, set[str]] = collections.defaultdict(set)
    for row in conn.execute(
        f"SELECT cik, period_end FROM filing WHERE period_end IS NOT NULL "
        f"AND form IN ({','.join('?' * len(ANNUAL_FORMS))})",
        ANNUAL_FORMS,
    ):
        reported[row["cik"]].add(row["period_end"])

    rows = [
        dict(r)
        for r in conn.execute(
            "SELECT id, cik, concept_key, product, unit, period_end, value, "
            "extracted_by FROM fact WHERE extracted_by LIKE 'table:%'"
        )
    ]
    # The same cell at a period the filer did report, which is what makes a
    # mismatched row a duplicate rather than the only reading of that figure.
    real: set[tuple] = {
        (r["cik"], r["concept_key"], r["product"], r["unit"],
         r["extracted_by"], r["period_end"][:4])
        for r in rows
        if r["period_end"] in reported.get(r["cik"], ())
    }

    doomed: list[dict] = []
    orphans: list[dict] = []
    for r in rows:
        if r["period_end"] in reported.get(r["cik"], ()):
            continue
        key = (r["cik"], r["concept_key"], r["product"], r["unit"],
               r["extracted_by"], r["period_end"][:4])
        (doomed if key in real else orphans).append(r)

    tickers = {c["cik"]: c["ticker"] for c in conn.execute("SELECT cik, ticker FROM company")}
    print(f"table-read rows: {len(rows):,}")
    print(f"  at a period the filer reported          {len(rows) - len(doomed) - len(orphans):>7,}")
    print(f"  at an unreported period, cell exists    {len(doomed):>7,}  <- to delete")
    print(f"  at an unreported period, cell does not  {len(orphans):>7,}  <- kept, reported below")

    if doomed:
        by = collections.Counter(
            (tickers.get(r["cik"]) or r["cik"][-6:], r["extracted_by"]) for r in doomed
        )
        print("\nto delete, by filer and source:")
        for (ticker, source), n in by.most_common(20):
            print(f"   {ticker:<7}{source:<20}{n:>6}")
    if orphans:
        by = collections.Counter(
            (tickers.get(r["cik"]) or r["cik"][-6:], r["period_end"]) for r in orphans
        )
        print(f"\nkept -- no counterpart at a reported period ({len(orphans)}):")
        for (ticker, period), n in by.most_common(12):
            print(f"   {ticker:<7}{period}  {n}")

    if not args.apply:
        print("\nreport only; pass --apply to delete")
        return 0

    ids = [(r["id"],) for r in doomed]
    with conn:
        for table in ("fact_verification", "fact_scale", "vision_check"):
            cursor = conn.executemany(f"DELETE FROM {table} WHERE fact_id = ?", ids)
            if cursor.rowcount > 0:
                print(f"  {table}: {cursor.rowcount} dependent row(s) removed")
        conn.executemany("DELETE FROM fact WHERE id = ?", ids)
    print(f"\napplied: {len(doomed):,} rows deleted")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
