"""Reconcile stored tickers against the SEC's canonical company_tickers.json.

Ticker is Basin's presentation identity, so it has to come from a source that is
actually maintained. The submissions API is not one: its ``tickers`` field is
empty for a number of listed filers and for every delisted one, which is how 14
of the first 94 companies ended up with a blank ticker.

Absence from company_tickers.json is a finding, not a failure -- the file lists
currently listed securities only, so a filer missing from it has no live
listing. Those rows are reported and left with a NULL ticker.

    python scripts/sync_tickers.py            # report only
    python scripts/sync_tickers.py --apply    # write the backfill
"""

from __future__ import annotations

import argparse
from pathlib import Path

from basin.edgar import EdgarClient
from basin.edgar.tickers import fetch_ticker_map
from basin.store import DEFAULT_DB_PATH, connect


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true", help="write changes")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    with EdgarClient() as client:
        ticker_map = fetch_ticker_map(client)
    print(f"SEC map: {len(ticker_map)} CIKs currently listed")

    conn = connect(args.db)
    rows = conn.execute("SELECT cik, name, ticker FROM company ORDER BY name").fetchall()

    backfill: list[tuple[str, str, str]] = []
    conflict: list[tuple[str, str, str, str]] = []
    unlisted: list[tuple[str, str]] = []

    for cik, name, stored in rows:
        stored = (stored or "").strip() or None
        chosen = ticker_map.primary(cik)
        if chosen is None:
            unlisted.append((cik, name))
        elif stored is None:
            backfill.append((cik, name, chosen))
        elif stored != chosen:
            conflict.append((cik, name, stored, chosen))

    print(f"\nbackfill {len(backfill)} | conflicts {len(conflict)} | unlisted {len(unlisted)}")
    for cik, name, chosen in backfill:
        print(f"  + {chosen:<8} {name[:50]}  ({cik})")
    for cik, name, stored, chosen in conflict:
        print(f"  ! {stored} -> {chosen:<8} {name[:44]}  ({cik})")
    for cik, name in unlisted:
        print(f"  - no listing  {name[:50]}  ({cik})")

    if not args.apply:
        print("\nreport only; pass --apply to write")
        return 0

    with conn:
        # '' and NULL both mean "no ticker"; only NULL survives the partial
        # unique index, so the empty strings are normalised before it is built.
        normalised = conn.execute(
            "UPDATE company SET ticker = NULL WHERE ticker = ''"
        ).rowcount
        for cik, _name, chosen in backfill:
            conn.execute("UPDATE company SET ticker = ? WHERE cik = ?", (chosen, cik))
        for cik, _name, _stored, chosen in conflict:
            conn.execute("UPDATE company SET ticker = ? WHERE cik = ?", (chosen, cik))
        conn.execute(
            "CREATE UNIQUE INDEX IF NOT EXISTS company_ticker_idx "
            "ON company (ticker) WHERE ticker IS NOT NULL"
        )
    print(f"\napplied: {normalised} blanks -> NULL, "
          f"{len(backfill)} backfilled, {len(conflict)} corrected, unique index built")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
