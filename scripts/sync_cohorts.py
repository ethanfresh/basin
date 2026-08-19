"""Assign every producing energy filer to a cohort, from Finviz's classification.

Cohort membership decides what a company can be compared against, so it has to
come from a maintained classification rather than a guess. It previously came
from matching substrings against company names, which cannot tell a royalty
vehicle from an operator when the name does not say so.

Finviz supplies the industry; the SEC's company_tickers.json supplies the CIK
that Basin actually keys on. Tickers that do not resolve to a CIK are reported,
never dropped silently -- a filer Basin cannot reach is a coverage fact.

    python scripts/sync_cohorts.py                 # report only
    python scripts/sync_cohorts.py --apply         # write companies + cohorts
    python scripts/sync_cohorts.py --all-energy    # all 8, not just producers
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
from pathlib import Path

import httpx

from basin.edgar import EdgarClient
from basin.edgar.tickers import fetch_ticker_map, primary_ticker
from basin.finviz import (
    ENERGY_COHORTS,
    EXCLUDED_TICKERS,
    PRODUCING_COHORTS,
    ScreenerRow,
    fetch_energy_cohorts,
)
from basin.store import DEFAULT_DB_PATH, connect
from basin.store.db import upsert_company

# Royalty, minerals and trust vehicles sit in the E&P industry but hold no
# operations: they own an interest in production someone else lifts, so they
# report no lifting cost and no capex. They stay in the cohort -- they are real
# comparables for each other -- but the flag keeps them out of operator peer
# tables, which is the distinction the README says coverage ranking gets wrong.
NON_OPERATOR_HINTS = ("royalt", "minerals", "trust")


def looks_like_operator(name: str) -> bool:
    lowered = name.lower()
    return not any(hint in lowered for hint in NON_OPERATOR_HINTS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true", help="write to the store")
    parser.add_argument(
        "--all-energy", action="store_true",
        help="all 8 energy industries, not only the producing ones",
    )
    parser.add_argument(
        "--csv", type=Path, default=Path("data/finviz_cohorts.csv"),
        help="where to save the pull, so the assignment is auditable later",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    slugs = ENERGY_COHORTS if args.all_energy else PRODUCING_COHORTS
    as_of = dt.date.today().isoformat()

    with httpx.Client(timeout=60.0) as http:
        cohorts = fetch_energy_cohorts(http, slugs=slugs)

    rows: list[ScreenerRow] = []
    for industry, members in cohorts.items():
        kept = [r for r in members if r.ticker not in EXCLUDED_TICKERS]
        dropped = len(members) - len(kept)
        print(f"{industry:<32} {len(kept):>4}" + (f"  ({dropped} excluded)" if dropped else ""))
        rows.extend(kept)
    print(f"{'TOTAL':<32} {len(rows):>4}\n")

    _write_csv(args.csv, rows, as_of)
    print(f"saved {args.csv}")

    with EdgarClient() as client:
        ticker_map = fetch_ticker_map(client)
    sec_by_ticker: dict[str, str] = {}
    for cik, tickers in ticker_map.by_cik.items():
        for ticker in tickers:
            sec_by_ticker.setdefault(ticker, cik)

    matched = [(r, sec_by_ticker[r.ticker]) for r in rows if r.ticker in sec_by_ticker]
    unresolved = [r for r in rows if r.ticker not in sec_by_ticker]
    resolved, collapsed = _collapse_share_classes(matched)

    print(f"\nresolved {len(matched)} tickers -> {len(resolved)} issuers "
          f"| unresolved {len(unresolved)}")
    for r in unresolved:
        print(f"  ? {r.ticker:<7} {r.company[:40]:<40} {r.country}")
    for cik, kept, dropped in collapsed:
        print(f"  = {kept:<7} absorbs {', '.join(dropped):<12} "
              f"same filer, one CIK ({cik})")

    conn = connect(args.db)

    # Follow a change of registrant before deciding anything about membership.
    #
    # Finviz's XOM resolves to CIK 2115436, the successor registrant created by
    # Exxon's 2026 redomiciliation. Every 10-K is on CIK 34088, which is where
    # the ticker and the facts live. Without this substitution the row holding
    # the data looks like a company Finviz has never heard of, and the empty
    # successor looks like the cohort member -- so the reconciliation below
    # would drop the wrong one.
    superseded = {
        r["successor_cik"]: r["predecessor_cik"]
        for r in conn.execute(
            "SELECT successor_cik, predecessor_cik FROM registrant_succession "
            "WHERE status = 'resolved' AND predecessor_cik IS NOT NULL"
        )
    }
    if superseded:
        resolved = [(row, superseded.get(cik, cik)) for row, cik in resolved]
        for successor, predecessor in superseded.items():
            print(f"  ~ {successor} superseded; cohort follows the history to "
                  f"{predecessor}")

    known = {cik for (cik,) in conn.execute("SELECT cik FROM company")}
    new = [(r, c) for r, c in resolved if c not in known]
    foreign = [(r, c) for r, c in resolved if not r.is_usa]
    print(f"already in store {len(resolved) - len(new)} | new {len(new)} "
          f"| foreign-domiciled {len(foreign)} (file 20-F/40-F, not 10-K)")

    # Reconcile, do not merely add. The cohort is defined as "the companies
    # Finviz currently places in these industries", so a member that is no
    # longer in the pull -- delisted, reclassified, or excluded on evidence --
    # has to leave. Previously this script could only add, which meant a stale
    # member stayed in every peer table indefinitely.
    #
    # Membership is cleared, never deleted. The facts, filings and citations
    # stay exactly as they were: the store is append-only, and a company being
    # out of scope is not a reason to destroy history that other rows cite.
    member_ciks = {cik for _row, cik in resolved}
    stale = conn.execute(
        "SELECT cik, ticker, name, cohort FROM company "
        "WHERE cohort IS NOT NULL ORDER BY cohort, ticker"
    ).fetchall()
    stale = [r for r in stale if r["cik"] not in member_ciks]

    def drop_reason(row) -> str:
        ticker = (row["ticker"] or "").upper()
        if ticker in EXCLUDED_TICKERS:
            return EXCLUDED_TICKERS[ticker]
        if row["cik"] in superseded:
            return (f"superseded registrant; cohort membership follows the "
                    f"filing history to {superseded[row['cik']]}")
        return "not in the current Finviz pull for these industries"

    if stale:
        print(f"\nno longer in the Finviz {'/'.join(sorted(set(slugs.values())))} "
              f"set -- {len(stale)} to drop:")
        for row in stale:
            reason = drop_reason(row)
            print(f"  - {row['ticker'] or '-':<6} {row['name'][:40]:<40} {row['cohort']}")
            print(f"         {reason[:96]}")

    if not args.apply:
        print("\nreport only; pass --apply to write")
        return 0

    with conn:
        for row, cik in resolved:
            upsert_company(
                conn, cik, row.company,
                ticker=row.ticker,
                is_operator=looks_like_operator(row.company),
                cohort=row.industry,
                cohort_source="finviz",
                cohort_as_of=as_of,
                country=row.country,
                market_cap_musd=row.market_cap,
            )
        for row in stale:
            conn.execute(
                "UPDATE company SET cohort = NULL, cohort_source = NULL, "
                "cohort_as_of = NULL, notes = ? WHERE cik = ?",
                (f"dropped from {row['cohort']} on {as_of}: {drop_reason(row)}",
                 row["cik"]),
            )

    print(f"\napplied: {len(resolved)} companies upserted with cohort as of "
          f"{as_of}, {len(stale)} dropped")
    return 0


def _collapse_share_classes(
    matched: list[tuple[ScreenerRow, str]]
) -> tuple[list[tuple[ScreenerRow, str]], list[tuple[str, str, list[str]]]]:
    """Reduce several listed share classes of one filer to a single company.

    Petrobras lists common (PBR) and preferred (PBR-A) separately, and Finviz
    screens them as two rows. They are one registrant with one CIK and one set
    of filings, so ingesting both would upsert the same company twice and count
    it twice in every cohort total. The common share class is kept.

    Reported rather than silently deduped: a ticker vanishing from a cohort
    should be a line of output, not a discrepancy noticed later in a count.
    """
    groups: dict[str, list[ScreenerRow]] = {}
    for row, cik in matched:
        groups.setdefault(cik, []).append(row)

    kept: list[tuple[ScreenerRow, str]] = []
    collapsed: list[tuple[str, str, list[str]]] = []
    for cik, members in groups.items():
        if len(members) == 1:
            kept.append((members[0], cik))
            continue
        winner = primary_ticker([m.ticker for m in members])
        chosen = next(m for m in members if m.ticker == winner)
        kept.append((chosen, cik))
        collapsed.append(
            (cik, chosen.ticker, [m.ticker for m in members if m.ticker != winner])
        )
    return kept, collapsed


def _write_csv(path: Path, rows: list[ScreenerRow], as_of: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["ticker", "company", "sector", "industry", "country",
             "market_cap_musd", "as_of"]
        )
        for r in rows:
            writer.writerow(
                [r.ticker, r.company, r.sector, r.industry, r.country,
                 r.market_cap, as_of]
            )


if __name__ == "__main__":
    raise SystemExit(main())
