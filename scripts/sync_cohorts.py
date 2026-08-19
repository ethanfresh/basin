"""Assign every producing energy filer to a cohort, from the SEC's SIC codes.

Cohort membership decides what a company can be compared against, so it has to
come from a maintained classification. It came from the Finviz Elite screener
until that dependency was removed: Finviz classifies better than SIC, but it is
a licensed feed whose terms do not permit redistributing the classification
inside a product, and it was the only non-public source in the pipeline.

EDGAR assigns every filer a SIC code and lets it be enumerated in reverse, which
is what membership needs. ``basin.cohorts`` holds the code-to-cohort map, the
thirteen filers whose code does not describe them, and the filers that sit in a
producing code and produce nothing.

SIC is coarser than Finviz, so the code proposes and the filing disposes: a
candidate joins the cohort only when ``producer_check`` records that a filing
was read and reserves were found. Candidates with no verdict are reported and
held out, never admitted quietly -- SIC 1311 sweeps in shells, midstream
partnerships, refiners and a biotechnology company.

    python scripts/sync_cohorts.py                 # report only
    python scripts/sync_cohorts.py --apply         # write companies + cohorts
    python scripts/sync_cohorts.py --survey        # also list non-producing SIC
"""

from __future__ import annotations

import argparse
import csv
import datetime as dt
import sys
from pathlib import Path

from basin.cohorts import EXCLUDED, NON_PRODUCING_SIC, cohort_for, is_operator, producing_sic
from basin.edgar import EdgarClient, SECError
from basin.edgar.tickers import primary_ticker
from basin.edgar.discovery import (
    FilerProfile,
    dedupe_issuers,
    fetch_profile,
    sic_ciks,
)
from basin.store import DEFAULT_DB_PATH, connect
from basin.store.db import upsert_company

# A filer that has not filed an annual report since this date is not a live
# comparable, whatever its SIC still says. Deregistered shells keep their code
# forever.
DEFAULT_SINCE = "2025-01-01"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true", help="write to the store")
    parser.add_argument(
        "--since", default=DEFAULT_SINCE,
        help="a filer is a candidate only if its latest 10-K, 20-F or 40-F is "
             "on or after this date (default: %(default)s)",
    )
    parser.add_argument(
        "--survey", action="store_true",
        help="also enumerate the non-producing oil & gas SIC codes, to show "
             "what the producing filter is excluding",
    )
    parser.add_argument(
        "--admit-unverified", action="store_true",
        help="admit candidates with no recorded producer verdict. Off by "
             "default: run scripts/check_producers.py instead",
    )
    parser.add_argument(
        "--csv", type=Path, default=Path("data/sic_cohorts.csv"),
        help="where to save the pull, so the assignment is auditable later",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    as_of = dt.date.today().isoformat()
    codes = producing_sic() + (tuple(NON_PRODUCING_SIC) if args.survey else ())

    try:
        profiles = _enumerate(codes, args.since)
    except SECError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    # Collapse CIKs belonging to one issuer before anything counts them. A
    # reorganisation gives a company a new CIK while the old one keeps its
    # filing history, so a naive count holds the same issuer twice.
    groups = dedupe_issuers(profiles)
    for g in groups:
        if g.superseded:
            others = ", ".join(f"{p.cik} {p.name or '(no name)'}" for p in g.superseded)
            print(f"  = {g.primary.cik} {g.primary.name[:36]:<36} absorbs {others}"
                  f"  [{g.reason}]")

    conn = connect(args.db)

    # Follow a change of registrant before deciding anything about membership.
    #
    # ExxonMobil Holdings (CIK 2115436) is the successor registrant created by
    # Exxon's 2026 redomiciliation. Every 10-K is on CIK 34088, which is where
    # the ticker and the facts live. Without this substitution the row holding
    # the data looks like a company EDGAR has never classified, and the empty
    # successor looks like the cohort member.
    superseded = {
        r["successor_cik"]: r["predecessor_cik"]
        for r in conn.execute(
            "SELECT successor_cik, predecessor_cik FROM registrant_succession "
            "WHERE status = 'resolved' AND predecessor_cik IS NOT NULL"
        )
    }

    known = {cik for (cik,) in conn.execute("SELECT cik FROM company")}
    tickers = {
        r["cik"]: r["ticker"] for r in conn.execute(
            "SELECT cik, ticker FROM company WHERE ticker IS NOT NULL"
        )
    }
    members = {
        cik for (cik,) in conn.execute(
            "SELECT cik FROM company WHERE cohort IS NOT NULL"
        )
    }
    verdicts = {
        r["cik"]: r["verdict"] for r in conn.execute(
            "SELECT cik, verdict FROM producer_check"
        )
    }

    admitted: list[tuple[FilerProfile, str, str, str]] = []   # profile, cik, cohort, source
    held: list[tuple[FilerProfile, str]] = []                 # profile, why
    passed: list[tuple[FilerProfile, str]] = []

    for group in groups:
        p = group.primary
        cik = superseded.get(p.cik, p.cik)
        if cik != p.cik:
            print(f"  ~ {p.cik} superseded; cohort follows the history to {cik}")

        if cik in EXCLUDED:
            passed.append((p, EXCLUDED[cik]))
            continue

        # Listing is tested here rather than during enumeration because a
        # succession splits one issuer's identity across two CIKs. Exxon files
        # every 10-K on CIK 34088 while the ticker sits on the 2026 successor
        # registrant, so 34088 looks unlisted until the store is consulted.
        if not (p.tickers or tickers.get(cik)):
            passed.append((p, "no listed security"))
            continue

        cohort, source = cohort_for(p)
        if cohort is None:
            passed.append((p, source))
            continue

        # A verdict recorded against the CIK the facts live on, not the shell.
        verdict = verdicts.get(cik) or verdicts.get(p.cik)
        if verdict == "non-producer":
            passed.append((p, "producer_check read the filing and found no reserves"))
            continue
        if verdict != "producer" and cik not in members and not args.admit_unverified:
            held.append((p, f"no producer verdict ({verdict or 'never checked'})"))
            continue

        admitted.append((p, cik, cohort, source))

    _report(admitted, held, passed, known, args)
    _write_csv(args.csv, admitted, as_of)
    print(f"\nsaved {args.csv}")

    # Reconcile, do not merely add. The cohort is defined as "the filers EDGAR
    # currently places in these SIC codes, whose filings show reserves", so a
    # member that is no longer in the pull -- deregistered, reclassified, or
    # excluded on evidence -- has to leave. Membership is cleared, never
    # deleted: the facts, filings and citations stay exactly as they were.
    member_ciks = {cik for _p, cik, _c, _s in admitted}
    stale = [
        r for r in conn.execute(
            "SELECT cik, ticker, name, cohort FROM company "
            "WHERE cohort IS NOT NULL ORDER BY cohort, ticker"
        ) if r["cik"] not in member_ciks
    ]

    def drop_reason(row) -> str:
        if row["cik"] in EXCLUDED:
            return EXCLUDED[row["cik"]]
        if row["cik"] in superseded:
            return (f"superseded registrant; cohort membership follows the "
                    f"filing history to {superseded[row['cik']]}")
        return "not in the current EDGAR pull for these SIC codes"

    if stale:
        print(f"\nno longer in the SIC {'/'.join(producing_sic())} set "
              f"-- {len(stale)} to drop:")
        for row in stale:
            print(f"  - {row['ticker'] or '-':<6} {row['name'][:40]:<40} {row['cohort']}")
            print(f"         {drop_reason(row)[:96]}")

    if not args.apply:
        print("\nreport only; pass --apply to write")
        return 0

    with conn:
        for p, cik, cohort, source in admitted:
            upsert_company(
                conn, cik, p.name,
                # EDGAR lists every security on the registrant, so a filer can
                # carry a warrant or a preferred class alongside its common.
                # Petrobras files PBR and PBR-A against one CIK; labelling its
                # facts with the preferred ADR would be wrong.
                ticker=primary_ticker(list(p.tickers)),
                is_operator=is_operator(p),
                cohort=cohort,
                cohort_source=source,
                cohort_as_of=as_of,
                country=p.country,
            )
        for row in stale:
            conn.execute(
                "UPDATE company SET cohort = NULL, cohort_source = NULL, "
                "cohort_as_of = NULL, notes = ? WHERE cik = ?",
                (f"dropped from {row['cohort']} on {as_of}: {drop_reason(row)}",
                 row["cik"]),
            )

    print(f"\napplied: {len(admitted)} companies upserted with cohort as of "
          f"{as_of}, {len(stale)} dropped")
    return 0


def _enumerate(codes: tuple[str, ...], since: str) -> list[FilerProfile]:
    """Every filer under *codes* that is still reporting, or has yet to start.

    A filer whose last annual report predates *since* is not a live comparable,
    whatever its SIC still says -- a deregistered shell keeps its code forever.
    A filer with no annual report at all is a different case and is kept: it is
    a recent registrant that has not reached its first one, not a stale member,
    and the producer check downstream is what decides whether it belongs. The
    distinction matters because collapsing the two would quietly drop a company
    for being new.

    Listing is not tested here. It is tested against the store in ``main``,
    where a succession that splits a ticker from a filing history can be seen.
    """
    profiles: list[FilerProfile] = []
    seen: set[str] = set()

    with EdgarClient() as client:
        for sic in codes:
            print(f"enumerating SIC {sic} filers with an annual report …")
            ciks = [c for c in sic_ciks(client, sic) if c not in seen]
            seen.update(ciks)
            print(f"  {len(ciks)} CIKs")

            kept = 0
            for n, cik in enumerate(ciks, 1):
                try:
                    p = fetch_profile(client, cik)
                except SECError as exc:
                    print(f"  ! {cik}: {exc}", file=sys.stderr)
                    continue
                if p.filed_annual_since(since) or p.latest_annual_date is None:
                    profiles.append(p)
                    kept += 1
                if n % 200 == 0:
                    print(f"  profiled {n}/{len(ciks)}")
            print(f"  {kept} reporting since {since}, or not yet reporting")

    return profiles


def _report(admitted, held, passed, known, args) -> None:
    new = [(p, cik) for p, cik, _c, _s in admitted if cik not in known]
    foreign = [p for p, _cik, _c, _s in admitted if p.is_foreign]
    overridden = [(p, c) for p, _cik, c, s in admitted if s == "sic-override"]

    print(f"\n{'=' * 68}")
    print(f"  admitted to a cohort                    {len(admitted):>5}")
    print(f"    already in store                      {len(admitted) - len(new):>5}")
    print(f"    new                                   {len(new):>5}")
    print(f"    foreign-domiciled (20-F/40-F, not 10-K) {len(foreign):>3}")
    print(f"    SIC overridden                        {len(overridden):>5}")
    print(f"  held: no producer verdict yet           {len(held):>5}")
    print(f"  passed over                             {len(passed):>5}")

    if overridden:
        print("\nEDGAR's SIC overridden:")
        for p, cohort in overridden:
            print(f"  {'|'.join(p.tickers)[:8]:<8} {p.name[:34]:<34} "
                  f"SIC {p.sic} -> {cohort}")

    if held:
        print(f"\nheld out of the cohort -- SIC proposes them, no filing has "
              f"confirmed reserves ({len(held)}):")
        for p, why in sorted(held, key=lambda h: h[0].name):
            print(f"  ? {'|'.join(p.tickers)[:8]:<8} {p.name[:38]:<38} "
                  f"SIC {p.sic}  {why}")
        print("\n  run: python scripts/check_producers.py --apply, then re-run "
              "this script")

    if args.survey:
        print(f"\npassed over ({len(passed)}):")
        for p, why in sorted(passed, key=lambda h: h[0].name):
            print(f"  x {'|'.join(p.tickers)[:8]:<8} {p.name[:38]:<38} {why[:60]}")


def _write_csv(path: Path, admitted, as_of: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            ["cik", "ticker", "name", "sic", "sic_description", "cohort",
             "cohort_source", "country", "latest_annual_form",
             "latest_annual_date", "as_of"]
        )
        for p, cik, cohort, source in sorted(admitted, key=lambda a: a[0].name):
            writer.writerow(
                [cik, "|".join(p.tickers), p.name, p.sic, p.sic_description,
                 cohort, source, p.country or "", p.latest_annual_form or "",
                 p.latest_annual_date or "", as_of]
            )


if __name__ == "__main__":
    raise SystemExit(main())
