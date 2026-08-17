"""Per-company XBRL coverage report.

Answers, against live SEC data, the question the cohort decision depends on:
for each candidate filer, which of Basin's concepts are actually tagged, and
under which taxonomy alias.

    export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"
    python scripts/coverage_report.py --cik 1090012 --cik 1656423
    python scripts/coverage_report.py --file cohort.txt --store data/basin.db
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from basin.edgar import EdgarClient, SECError
from basin.facts import ALL_CONCEPTS, coverage_for_company
from basin.facts.concepts import FACTS_LAYER_CONCEPTS
from basin.store import connect, record_coverage


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--cik", action="append", default=[], help="CIK to check; repeatable"
    )
    parser.add_argument(
        "--file", type=Path, help="file of CIKs, one per line (# comments allowed)"
    )
    parser.add_argument(
        "--store", type=Path, help="also write snapshots to this SQLite store"
    )
    parser.add_argument(
        "--all-concepts",
        action="store_true",
        help="include the known-gap concepts, not just the Facts layer",
    )
    parser.add_argument(
        "--current-since",
        default="2023-01-01",
        help="a concept counts as current only if its latest period ends on or "
        "after this date (default: %(default)s)",
    )
    return parser.parse_args(argv)


def load_ciks(args: argparse.Namespace) -> list[str]:
    ciks = list(args.cik)
    if args.file:
        for line in args.file.read_text().splitlines():
            line = line.split("#", 1)[0].strip()
            if line:
                ciks.append(line.split(",")[0].strip())
    return ciks


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    ciks = load_ciks(args)
    if not ciks:
        print("no CIKs given; use --cik or --file", file=sys.stderr)
        return 2

    concepts = ALL_CONCEPTS if args.all_concepts else FACTS_LAYER_CONCEPTS
    conn = connect(args.store) if args.store else None

    try:
        with EdgarClient() as client:
            results = []
            for cik in ciks:
                coverage = coverage_for_company(client, cik, concepts)
                results.append(coverage)
                if conn is not None:
                    record_coverage(conn, coverage)
                _print_company(coverage, len(concepts), args.current_since)
    except SECError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        if conn is not None:
            conn.commit()
            conn.close()

    _print_totals(results, concepts, args.current_since)
    return 0


def _is_current(concept_coverage, since: str) -> bool:
    """Tagged is not the same as usable.

    Several filers tag a reserve concept in old filings and stop; the panel
    needs recent periods, so staleness is counted separately from absence.
    """
    return bool(
        concept_coverage.tagged
        and concept_coverage.latest_period_end
        and concept_coverage.latest_period_end >= since
    )


def _print_company(coverage, total: int, since: str) -> None:
    name = coverage.entity_name or "(unknown)"
    current = sum(1 for c in coverage.concepts if _is_current(c, since))
    print(
        f"\n{coverage.cik}  {name}  —  "
        f"{current}/{total} current, {coverage.tagged_count}/{total} ever tagged"
    )
    if coverage.error:
        print(f"  ! {coverage.error}")
    for c in coverage.concepts:
        if not c.tagged:
            print(f"  · {c.concept_key:<34} untagged")
            continue
        mark = "✓" if _is_current(c, since) else "~"
        stale = "" if _is_current(c, since) else "  STALE"
        units = ",".join(c.units) or "-"
        print(
            f"  {mark} {c.concept_key:<34} {c.taxonomy}:{c.tag}"
            f"  [{units}]  n={c.observation_count}"
            f"  latest={c.latest_period_end}{stale}"
        )


def _print_totals(results, concepts, since: str) -> None:
    n = len(results)
    print(f"\n{'=' * 72}\nconcept coverage across {n} filers (current = period ≥ {since})")
    print(f"  {'concept':<34} {'current':>9}  {'ever':>7}")
    for concept in concepts:
        matches = [c for r in results for c in r.concepts if c.concept_key == concept.key]
        ever = sum(1 for c in matches if c.tagged)
        current = sum(1 for c in matches if _is_current(c, since))
        print(f"  {concept.key:<34} {current:>6}/{n}  {ever:>4}/{n}")


if __name__ == "__main__":
    raise SystemExit(main())
