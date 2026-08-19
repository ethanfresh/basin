"""Ingest XBRL facts for one or more filers into the fact store.

    export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"
    python scripts/ingest_xbrl.py --cik 1090012 --cik 1539838

One request per company: ``companyfacts`` carries every tagged value, so the
concept registry is applied to the payload locally rather than over the wire.

Filings are registered from the facts themselves — each observation names the
accession, form and filing date that produced it — so a fact can always be
resolved back to a document without a second API call.

The reserve family is chosen per filer by arithmetic (:mod:`basin.facts.valid\
ation`) and applied per period. A tag whose meaning changed mid-history has no
combination that is right for the whole of it, so the periods where the chosen
combination fails developed + undeveloped = total are not written. Continental
tags the total under ``ProvedUndevelopedReserveBOE1`` from FY2021 on, and the
undeveloped figure under the tag named for the total; writing those five years
put a 2.3x error in the panel where the identity had already said not to.
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


def _drop_incoherent(rows, validation):
    """Remove reserve rows for the periods the identity check rejected.

    Scoped by tag, not merely by concept: the validator's verdict is about the
    combination of tags it chose, so a row the payload carries under a
    different alias was never tested and is not implicated. Scoped by period
    for the same reason -- the same tag is correct for Continental's FY2013
    through FY2020 and wrong afterwards, and dropping the filer's whole history
    over five bad years would trade one wrong answer for eight missing ones.
    """
    if not validation.incoherent_period_ends:
        return rows, 0
    rejected = {
        (key, taxonomy, tag)
        for key, (taxonomy, tag) in validation.overrides.items()
    }
    kept = [
        r for r in rows
        if not (
            (r.concept_key, r.taxonomy, r.tag) in rejected
            and r.period_end in validation.incoherent_period_ends
        )
    ]
    return kept, len(rows) - len(kept)


def _prune_incoherent(conn, validation) -> int:
    """Delete reserve rows a previous run wrote for periods now known bad.

    Deliberately narrow: same filer, same concept, same tag, same rejected
    period, and only rows the companyfacts path wrote. Inline-XBRL and
    table-read rows for those periods are untouched — they are independent
    readings, and in Continental's case they are the ones that close.
    """
    if not validation.incoherent_period_ends:
        return 0
    periods = sorted(validation.incoherent_period_ends)
    removed = 0
    for key, (taxonomy, tag) in validation.overrides.items():
        cursor = conn.execute(
            "DELETE FROM fact WHERE cik = ? AND concept_key = ? AND taxonomy = ? "
            f"AND tag = ? AND extracted_by = 'xbrl' "
            f"AND period_end IN ({','.join('?' * len(periods))})",
            (validation.cik, key, taxonomy, tag, *periods),
        )
        removed += cursor.rowcount
    return removed


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
                rows, suppressed = _drop_incoherent(rows, validation)
                # A row this run has just decided not to write, but a previous
                # run did, is still in the store saying the wrong thing. The
                # verdict has to reach the rows it invalidates, not only the
                # ones it prevents.
                pruned = _prune_incoherent(conn, validation)
                upsert_company(conn, cik_padded(payload["cik"]), payload.get("entityName", ""))

                # Register every filing the rows cite before writing the rows;
                # the foreign key exists to make an uncitable fact impossible.
                for row in rows:
                    record_filing(conn, row.accession, row.cik, row.form, row.filed)

                written = insert_facts(conn, rows)
                record_alias_validation(conn, validation)
                conn.commit()
                # .get, not [], so adding a status to the validator never
                # crashes an ingest again -- 'drifted' did exactly that.
                mark = {
                    "validated": "ok",
                    "drifted": "DRIFTED",
                    "incoherent": "INCOHERENT",
                    "insufficient": "--",
                }.get(validation.status, validation.status)
                print(
                    f"{cik_padded(payload['cik'])}  {payload.get('entityName', '')[:34]:<36}"
                    f"{written:>5} new / {len(rows):>5} rows  "
                    f"{len({r.concept_key for r in rows})} concepts  "
                    f"reserves:{mark}"
                    + (f" {validation.coherent_periods}/{validation.tested_periods}"
                       if validation.tested_periods else "")
                    + (f"  −{suppressed} rows in "
                       f"{len(validation.incoherent_period_ends)} bad period(s)"
                       if suppressed else "")
                    + (f", {pruned} previously stored removed" if pruned else "")
                )
    except SECError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.close()

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
