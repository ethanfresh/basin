"""Follow cohort tickers across a change of registrant.

A ticker whose CIK holds no 10-K is usually not a filer that stopped reporting;
it is a filer that was replaced. This finds the registrant that was superseded,
records the link with the 8-K12B that establishes it, and moves the ticker onto
the CIK that actually holds the filing history -- because ticker is Basin's
presentation identity, and it should point at the row with the data in it.

    python scripts/resolve_succession.py
    python scripts/resolve_succession.py --apply
"""

from __future__ import annotations

import argparse
from pathlib import Path

from basin.edgar import EdgarClient
from basin.edgar.succession import Succession, find_succession
from basin.store import DEFAULT_DB_PATH, connect
from basin.store.db import record_succession, upsert_company


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cik", action="append", default=[],
                        help="check only these CIKs (default: cohort members with no facts)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.db)

    if args.cik:
        candidates = [(c, "", "") for c in args.cik]
    else:
        candidates = [
            (r["cik"], r["ticker"] or "", r["name"])
            for r in conn.execute(
                """SELECT cik, ticker, name FROM company
                   WHERE cohort IS NOT NULL
                     AND NOT EXISTS (SELECT 1 FROM fact f WHERE f.cik = company.cik)
                   ORDER BY name"""
            )
        ]
    print(f"checking {len(candidates)} registrants with no facts\n")

    found: list[tuple[Succession, str]] = []
    with EdgarClient() as client:
        for cik, ticker, _name in candidates:
            result = find_succession(client, cik)
            if result.status == "none":
                continue
            found.append((result, ticker))
            arrow = result.predecessor_cik or "?"
            print(f"  {ticker or '-':<6} {result.successor_name[:34]:<34} "
                  f"{result.successor_cik} -> {arrow}  {result.status}"
                  + (f"  ({result.note})" if result.note else ""))
            if result.predecessor_name:
                print(f"         predecessor named in {result.accession}: "
                      f"{result.predecessor_name}")

    resolved = [(s, t) for s, t in found if s.status == "resolved"]
    print(f"\nsuccessions found {len(found)} | resolved {len(resolved)}")

    if not args.apply:
        print("\nreport only; pass --apply to write")
        return 0

    with conn:
        for succession, ticker in found:
            record_succession(conn, succession)
            if succession.status != "resolved":
                continue
            row = conn.execute(
                "SELECT cohort, cohort_source, cohort_as_of, country, market_cap_musd,"
                "       is_operator FROM company WHERE cik = ?",
                (succession.successor_cik,),
            ).fetchone()

            # The ticker moves to the registrant holding the history. It cannot
            # sit on both -- the unique index says so -- and the row worth
            # showing is the one with facts behind it.
            if ticker:
                conn.execute(
                    "UPDATE company SET ticker = NULL WHERE cik = ?",
                    (succession.successor_cik,),
                )
            upsert_company(
                conn,
                succession.predecessor_cik,
                succession.predecessor_name or "",
                ticker=ticker or None,
                cohort=row["cohort"] if row else None,
                cohort_source=row["cohort_source"] if row else None,
                cohort_as_of=row["cohort_as_of"] if row else None,
                country=row["country"] if row else None,
                market_cap_musd=row["market_cap_musd"] if row else None,
                is_operator=bool(row["is_operator"]) if row else None,
                notes=f"predecessor registrant of {succession.successor_cik}, "
                      f"per {succession.accession}",
            )
            conn.execute(
                "UPDATE company SET notes = ? WHERE cik = ?",
                (f"successor registrant; history filed under "
                 f"{succession.predecessor_cik}", succession.successor_cik),
            )
    print(f"\napplied: {len(found)} recorded, {len(resolved)} tickers moved to the "
          f"registrant holding the history")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
