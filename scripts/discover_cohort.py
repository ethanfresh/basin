"""Enumerate the filers under one SIC code and profile each, to survey a cohort.

    export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"
    python scripts/discover_cohort.py --out data/cohort_candidates.csv

Writes one row per filer with the facts cohort selection turns on: last annual
report and which of 10-K/20-F/40-F it was, 8-K activity, listing status,
domicile. Profiles are cached under ``data/cache/submissions`` so re-runs and
later filtering cost no requests.

This is the survey tool. ``scripts/sync_cohorts.py`` is what actually assigns
membership, across every producing SIC code at once.
"""

from __future__ import annotations

import argparse
import csv
import json
import sys
from dataclasses import asdict
from pathlib import Path

from basin.edgar import EdgarClient, SECError, cik_padded
from basin.edgar.discovery import (
    FilerProfile,
    dedupe_issuers,
    profile_from_submissions,
    sic_ciks,
    submissions_url,
)

CACHE_DIR = Path("data/cache/submissions")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--sic", default="1311")
    parser.add_argument(
        "--since",
        default="2025-01-01",
        help="a filer is a candidate only if its latest 10-K, 20-F or 40-F is "
        "on or after this date (default: %(default)s)",
    )
    parser.add_argument("--out", type=Path, default=Path("data/cohort_candidates.csv"))
    parser.add_argument(
        "--refresh", action="store_true", help="ignore cached submissions payloads"
    )
    return parser.parse_args(argv)


def load_profile(client: EdgarClient, cik: str, *, refresh: bool) -> FilerProfile:
    """Fetch a profile, caching the raw payload so re-runs are free."""
    cached = CACHE_DIR / f"CIK{cik}.json"
    if cached.exists() and not refresh:
        return profile_from_submissions(json.loads(cached.read_text()))

    payload = client.get_json(submissions_url(cik))
    CACHE_DIR.mkdir(parents=True, exist_ok=True)
    cached.write_text(json.dumps(payload))
    return profile_from_submissions(payload)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)

    try:
        with EdgarClient() as client:
            print(f"enumerating SIC {args.sic} filers with an annual report …")
            ciks = sic_ciks(client, args.sic)
            print(f"  {len(ciks)} CIKs")

            profiles: list[FilerProfile] = []
            for n, cik in enumerate(ciks, 1):
                try:
                    profiles.append(load_profile(client, cik, refresh=args.refresh))
                except SECError as exc:
                    print(f"  ! {cik}: {exc}", file=sys.stderr)
                if n % 100 == 0:
                    print(f"  profiled {n}/{len(ciks)}")
    except SECError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1

    candidates = [p for p in profiles if p.filed_annual_since(args.since)]

    # Collapse CIKs belonging to one issuer before anything counts them.
    groups = dedupe_issuers(candidates)
    deduped = [g.primary for g in groups]

    _write_csv(args.out, deduped)
    _summarise(profiles, candidates, groups, deduped, args)
    return 0


def _write_csv(path: Path, profiles: list[FilerProfile]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="") as fh:
        writer = csv.writer(fh)
        writer.writerow(
            [
                "cik",
                "name",
                "tickers",
                "exchanges",
                "sic_description",
                "state_or_country",
                "country_description",
                "latest_annual_form",
                "latest_annual_date",
                "latest_annual_accession",
                "annual_count",
                "eightk_count",
                "latest_8k_date",
            ]
        )
        for p in sorted(profiles, key=lambda p: p.name):
            writer.writerow(
                [
                    p.cik,
                    p.name,
                    "|".join(p.tickers),
                    "|".join(p.exchanges),
                    p.sic_description,
                    p.state_or_country,
                    p.state_or_country_description,
                    p.latest_annual_form or "",
                    p.latest_annual_date or "",
                    p.latest_annual_accession or "",
                    p.annual_count,
                    p.eightk_count,
                    p.latest_8k_date or "",
                ]
            )


def _summarise(profiles, candidates, groups, deduped, args) -> None:
    listed = [p for p in deduped if p.tickers]
    foreign = [p for p in deduped if p.is_foreign]
    with_8k = [p for p in deduped if p.eightk_count]
    merged = [g for g in groups if g.superseded]

    print(f"\n{'=' * 64}")
    print(f"  CIKs with an annual report on file      {len(profiles):>5}")
    print(f"  reported since {args.since}            {len(candidates):>5}")
    print(f"  distinct issuers after CIK dedup        {len(deduped):>5}")
    print(f"    of those, currently ticker-listed     {len(listed):>5}")
    print(f"    of those, foreign business address    {len(foreign):>5}")
    print(f"    of those, that also file 8-Ks         {len(with_8k):>5}")

    if merged:
        print(f"\n  {len(merged)} issuer(s) filed under more than one CIK:")
        for g in merged:
            others = ", ".join(
                f"{p.cik} {p.name or '(no name)'}" for p in g.superseded
            )
            print(f"    {g.primary.cik} {g.primary.name}")
            print(f"      absorbed {others}  [{g.reason}]")

    print(f"\nwrote {args.out}")


if __name__ == "__main__":
    raise SystemExit(main())
