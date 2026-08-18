"""Ingest XBRL facts for one or more filers into the fact store.

    export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"
    python scripts/ingest_xbrl.py --cik 1090012 --cik 1539838

One request per company: ``companyfacts`` carries every tagged value, so the
concept registry is applied to the payload locally rather than over the wire.

Filings are registered from the facts themselves — each observation names the
accession, form and filing date that produced it — so a fact can always be
resolved back to a document without a second API call.
"""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

from basin.edgar import EdgarClient, NotFound, SECError, cik_padded
from basin.facts import ALL_CONCEPTS, fetch_companyfacts, rows_for_all_concepts
from basin.facts.validation import validate_reserve_family
from basin.store import (
    DEFAULT_DB_PATH,
    connect,
    insert_facts,
    record_alias_validation,
    record_filing,
    upsert_company,
)


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--cik", action="append", default=[], required=True)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument(
        "--forms",
        default="10-K,10-K/A",
        help="comma-separated forms to ingest, or 'all' (default: %(default)s)",
    )
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    forms = None if args.forms == "all" else tuple(args.forms.split(","))
    conn = connect(args.store)

    try:
        with EdgarClient() as client:
            for cik in args.cik:
                try:
                    payload = fetch_companyfacts(client, cik)
                except NotFound:
                    print(f"{cik_padded(cik)}  no companyfacts payload — skipped")
                    continue

                # Choose the reserve tags whose arithmetic holds for this
                # filer before reading any rows out of the payload.
                validation = validate_reserve_family(payload, forms=forms)
                rows = list(
                    rows_for_all_concepts(
                        payload,
                        ALL_CONCEPTS,
                        forms=forms,
                        alias_overrides=validation.overrides,
                        unit_overrides=validation.unit_overrides,
                    )
                )
                upsert_company(conn, cik_padded(payload["cik"]), payload.get("entityName", ""))

                # Register every filing the rows cite before writing the rows;
                # the foreign key exists to make an uncitable fact impossible.
                for row in rows:
                    record_filing(conn, row.accession, row.cik, row.form, row.filed)

                written = insert_facts(conn, rows)
                record_alias_validation(conn, validation)
                conn.commit()
                mark = {"validated": "ok", "incoherent": "INCOHERENT",
                        "insufficient": "--"}[validation.status]
                print(
                    f"{cik_padded(payload['cik'])}  {payload.get('entityName', '')[:34]:<36}"
                    f"{written:>5} new / {len(rows):>5} rows  "
                    f"{len({r.concept_key for r in rows})} concepts  "
                    f"reserves:{mark}"
                    + (f" {validation.coherent_periods}/{validation.tested_periods}"
                       if validation.tested_periods else "")
                )
    except SECError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
