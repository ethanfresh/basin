"""Load cohort metadata (ticker, basin, operator flag) into the store.

Ingest gets company names from ``companyfacts``, but tickers, basin and the
operator/non-operator distinction are cohort decisions that live in the CSV
produced by ``discover_cohort.py`` — and, eventually, in hand curation on top
of it.

    python scripts/load_cohort.py --csv data/cohort_candidates.csv
"""

from __future__ import annotations

import argparse
import csv
from pathlib import Path

from basin.store import DEFAULT_DB_PATH, connect

# SIC 1311 sweeps in vehicles that are not E&P operators. These are matched
# against the company name as a first pass; the roadmap's operator filter will
# replace this with a curated decision per company.
NON_OPERATOR_HINTS = (
    "royalt",
    "minerals",
    "trust",
    "midstream",
    "pipeline",
    "refin",
    "partners, l.p.",
)


def looks_like_operator(name: str) -> bool:
    lowered = name.lower()
    return not any(hint in lowered for hint in NON_OPERATOR_HINTS)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--csv", type=Path, default=Path("data/cohort_candidates.csv"))
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)

    updated = 0
    for row in csv.DictReader(args.csv.open()):
        ticker = (row["tickers"] or "").split("|")[0]
        # Only touch companies ingest already created, so this never invents a
        # cohort member that has no facts behind it.
        cursor = conn.execute(
            """
            UPDATE company
               SET ticker = ?, name = ?, is_operator = ?
             WHERE cik = ?
            """,
            (ticker, row["name"], int(looks_like_operator(row["name"])), row["cik"]),
        )
        updated += cursor.rowcount

    conn.commit()
    conn.close()
    print(f"updated {updated} companies from {args.csv}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
