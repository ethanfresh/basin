"""Test every cohort member against the one thing a producer cannot fake.

Cohort membership is Finviz's classification, and it carries errors -- TGS, an
Argentine gas pipeline, sits in Oil & Gas Integrated. A misclassified filer does
not fail loudly; it shows up as a blank row in a reserves panel, which reads as
a coverage gap rather than as a company with nothing to report.

Evidence, in order of cost: the reserve and production concepts a filer tags,
then the reserve language in its annual report. Either confirms a producer.
Only the absence of both, with a document actually read, is a negative.

    python scripts/check_producers.py
    python scripts/check_producers.py --apply     # record the verdicts
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.documents import corpus
from basin.facts.producer import (
    NON_PRODUCER,
    PRODUCER,
    RESERVE_CONCEPTS,
    UNKNOWN,
    ProducerCheck,
    count_reserve_phrases,
    flatten,
    judge,
)
from basin.store import DEFAULT_DB_PATH, connect
from basin.store.db import record_producer_check

# The annual report is where reserves are disclosed. A 10-Q or an 8-K carries
# no reserve section, so reading them would only add noise and requests.
ANNUAL_FORMS = ("10-K", "20-F", "40-F")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true")
    parser.add_argument("--cohort", action="append", default=[],
                        help="limit to these cohorts (default: all)")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.db)

    sql = ("SELECT cik, ticker, name, cohort, country, reporting_taxonomy "
           "FROM company WHERE cohort IS NOT NULL")
    params: tuple = ()
    if args.cohort:
        sql += f" AND cohort IN ({','.join('?' * len(args.cohort))})"
        params = tuple(args.cohort)
    sql += " ORDER BY cohort, ticker IS NULL, ticker"
    companies = conn.execute(sql, params).fetchall()
    print(f"checking {len(companies)} cohort members\n")

    results: list[tuple[ProducerCheck, str]] = []
    for row in companies:
        concepts = [
            r[0] for r in conn.execute(
                f"""SELECT DISTINCT concept_key FROM fact WHERE cik = ?
                    AND concept_key IN ({','.join('?' * len(RESERVE_CONCEPTS))})""",
                (row["cik"], *RESERVE_CONCEPTS),
            )
        ]
        hits, read, document = _read_annual_report(conn, row["cik"])
        check = judge(
            cik=row["cik"], ticker=row["ticker"], name=row["name"],
            concepts=concepts, phrase_hits=hits, documents_read=read,
            document=document,
        )
        results.append((check, row["cohort"]))
        if args.apply:
            with conn:
                record_producer_check(conn, check, row["cohort"])

    counts = collections.Counter(c.verdict for c, _ in results)
    print(f"producer {counts[PRODUCER]} | non-producer {counts[NON_PRODUCER]} "
          f"| unknown {counts[UNKNOWN]}\n")

    failed = [(c, k) for c, k in results if c.verdict == NON_PRODUCER]
    print("=" * 78)
    print(f"FAILS THE PRODUCER TEST -- {len(failed)} cohort members")
    print("=" * 78)
    for check, cohort in failed:
        print(f"\n  {check.ticker or '-':<6} {check.name[:44]:<44} {cohort}")
        print(f"         {check.note}")
        print(f"         read: {check.document}")

    unknown = [(c, k) for c, k in results if c.verdict == UNKNOWN]
    print("\n" + "=" * 78)
    print(f"UNTESTED -- {len(unknown)} members, no facts and no filing in the corpus")
    print("=" * 78)
    for check, cohort in unknown:
        print(f"  {check.ticker or '-':<6} {check.name[:44]:<44} {cohort}")

    # Producers confirmed only by the document are the ones the XBRL misses --
    # mostly IFRS filers, and the reason the second signal exists at all.
    doc_only = [c for c, _ in results if c.verdict == PRODUCER and not c.concepts]
    print("\n" + "=" * 78)
    print(f"CONFIRMED FROM THE FILING, NOT THE XBRL -- {len(doc_only)}")
    print("=" * 78)
    for check in doc_only:
        print(f"  {check.ticker or '-':<6} {check.name[:40]:<40} "
              f"{check.phrase_hits:>4} hits  {check.document}")

    if not args.apply:
        print("\nreport only; pass --apply to record the verdicts")
    return 0


def _read_annual_report(conn, cik: str) -> tuple[int, int, str | None]:
    """Scan the most recent annual report in the corpus for reserve language.

    Every stored document of that filing is read, not just the primary one: a
    40-F's reserve statements are in an attached Annual Information Form, and
    reading only the cover sheet is what this check exists to avoid repeating.
    """
    row = conn.execute(
        f"""SELECT accession FROM filing
            WHERE cik = ? AND substr(form, 1, 4) IN ({','.join('?' * len(ANNUAL_FORMS))})
            ORDER BY filed_date DESC LIMIT 1""",
        (cik, *[f[:4] for f in ANNUAL_FORMS]),
    ).fetchone()
    if row is None:
        return 0, 0, None

    accession = row["accession"]
    directory = corpus.CORPUS / accession
    if not directory.exists():
        return 0, 0, None

    total = 0
    read = 0
    best = (0, None)
    for path in sorted(directory.iterdir()):
        raw = corpus.load_raw(accession, path.name)
        if raw is None:
            continue
        read += 1
        hits = count_reserve_phrases(flatten(raw))
        total += hits
        if hits > best[0]:
            best = (hits, path.name)
    return total, read, best[1] or f"{accession} ({read} docs, no reserve language)"


if __name__ == "__main__":
    raise SystemExit(main())
