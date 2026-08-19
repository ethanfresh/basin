"""Record which filers are out of scope because they are not traded.

Basin's scope is traded US securities. Cohort membership comes from a screener,
and a screener returns listed securities, so a producer with no live listing is
excluded automatically -- silently, and without leaving any trace that it exists.

That is the wrong kind of boundary. Continental Resources went private in 2022
and still files 10-Ks against its public debt; Energy 11 and Energy Resources 12
are non-traded partnerships filing every year. They are producers, they are
current, and no screener will return them.

This marks every filer in the store with why it is or is not eligible, so the
gap is a measured number rather than something discovered later. Nothing is
deleted and no cohort changes -- the scope decision stands.

    python scripts/note_untraded.py
    python scripts/note_untraded.py --apply
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.edgar import EdgarClient, NotFound, SECError
from basin.edgar.discovery import submissions_url
from basin.edgar.tickers import fetch_ticker_map
from basin.facts.producer import RESERVE_CONCEPTS
from basin.store import DEFAULT_DB_PATH, connect

LISTED = "listed"
NOT_LISTED = "not-listed"
SUPERSEDED = "superseded"

# A filer that has submitted nothing for two years has stopped, whatever the
# reason -- acquired, wound up, gone dark. Worth separating from one that is
# still reporting, because only the second is a live gap in coverage.
DORMANT_AFTER = "2025-01-01"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.db)

    superseded = {
        r["successor_cik"]
        for r in conn.execute(
            "SELECT successor_cik FROM registrant_succession WHERE status = 'resolved'"
        )
    }
    companies = conn.execute(
        "SELECT cik, ticker, name, cohort FROM company ORDER BY name"
    ).fetchall()

    with EdgarClient() as client:
        ticker_map = fetch_ticker_map(client)
        rows = []
        for company in companies:
            cik = company["cik"]
            # Cohort membership is itself proof of a listing -- it comes from a
            # screener of traded securities. It has to count, because the SEC's
            # ticker map points a superseded company's symbol at the successor:
            # XOM maps to CIK 2115436 while the ticker, the facts and the cohort
            # membership all sit on predecessor 34088, which would otherwise be
            # reported as an unreachable filer.
            listed = ticker_map.primary(cik) is not None or company["cohort"] is not None
            last_filed = _last_filing(client, cik)
            concepts = [
                r[0] for r in conn.execute(
                    f"""SELECT DISTINCT concept_key FROM fact WHERE cik = ?
                        AND concept_key IN ({','.join('?' * len(RESERVE_CONCEPTS))})""",
                    (cik, *RESERVE_CONCEPTS),
                )
            ]
            rows.append((company, listed, last_filed, concepts))

    out = []
    for company, listed, last_filed, concepts in rows:
        cik = company["cik"]
        if cik in superseded:
            status, note = SUPERSEDED, "registrant replaced by a successor"
        elif listed:
            status, note = LISTED, None
        else:
            active = bool(last_filed and last_filed >= DORMANT_AFTER)
            has_reserves = bool(concepts)
            if active and has_reserves:
                note = ("still filing, no live listing -- a producer this scope "
                        "cannot reach")
            elif active:
                note = "still filing, no live listing"
            else:
                note = f"no live listing; last filed {last_filed or 'never'}"
            status = NOT_LISTED
        out.append((cik, company["name"], status, last_filed, note,
                    bool(concepts), last_filed and last_filed >= DORMANT_AFTER))

    counts = collections.Counter(r[2] for r in out)
    print(f"listed {counts[LISTED]} | not listed {counts[NOT_LISTED]} "
          f"| superseded {counts[SUPERSEDED]}\n")

    missed = [r for r in out if r[2] == NOT_LISTED and r[5] and r[6]]
    print("=" * 74)
    print(f"PRODUCERS THIS SCOPE CANNOT REACH -- {len(missed)}")
    print("  still filing, reporting reserve or production concepts, not traded")
    print("=" * 74)
    for cik, name, _s, last, _n, _c, _a in missed:
        print(f"  {name[:46]:<46} last filed {last}  ({cik})")

    stopped = [r for r in out if r[2] == NOT_LISTED and not r[6]]
    print(f"\nno longer filing -- {len(stopped)} (acquired, wound up, or dark)")
    for cik, name, _s, last, _n, _c, _a in stopped:
        print(f"  {name[:46]:<46} last filed {last or 'never'}")

    if not args.apply:
        print("\nreport only; pass --apply to write")
        return 0

    with conn:
        for cik, _name, status, last, note, _c, _a in out:
            conn.execute(
                "UPDATE company SET listing_status = ?, last_filing_date = ?, "
                "listing_note = ? WHERE cik = ?",
                (status, last, note, cik),
            )
    print(f"\napplied: {len(out)} companies marked")
    return 0


def _last_filing(client: EdgarClient, cik: str) -> str | None:
    try:
        payload = client.get_json(submissions_url(cik))
    except (NotFound, SECError):
        return None
    dates = payload.get("filings", {}).get("recent", {}).get("filingDate", [])
    return max(dates) if dates else None


if __name__ == "__main__":
    raise SystemExit(main())
