"""Fill in the fiscal period and primary document EDGAR already published.

    python scripts/backfill_filing_metadata.py            # report only
    python scripts/backfill_filing_metadata.py --apply
    python scripts/backfill_filing_metadata.py --apply --fetch   # + older blocks

``filing.period_end`` and ``filing.primary_doc`` are NULL on 2,404 of 3,679
rows, 883 of them 10-Ks, and both are load-bearing:

**period_end decides whether a reserve table can be read at all.** A reserve row
often dates itself ("As of December 31, 2025"), but where it does not, the
extractor falls back to the filing's fiscal period end. Diamondback's FY2025
10-K yields 0 readings with the fallback absent and 48 with it present -- the
table parses identically either way, and the filing metadata is the whole
difference.

**primary_doc decides what ``document.kind`` means.** ``index_documents.py``
labels a document ``primary`` only when its name matches this field, so a NULL
here labels every document of that filing an exhibit. ``fang-20251231.htm``,
``eog-20251231.htm`` and ``dvn-20251231.htm`` are each that filer's 10-K and the
only document in their accession, and all three currently read as exhibits.

The cause is not a fetch failure. ``record_filing`` registers filings from fact
rows, and a fact row carries an accession, a form and a filing date but neither
of these fields -- so anything the XBRL path registered has had them NULL since
ingest, and nothing backfills them. The submissions API publishes both, free,
alongside every filing, and the payloads are already cached on disk.

Two sources, in order of cost:

  * the cached ``recent`` block, which covers roughly the last thousand filings
    per filer and answers 1,952 of the 2,404 at no network cost
  * the older paginated blocks named in ``filings.files``, fetched only under
    ``--fetch``, which is what the rest need

Nothing is invented. A filing EDGAR reports no ``reportDate`` for keeps its NULL
and is counted, because an absent period is a fact about the filing -- some
forms genuinely have none.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from basin.edgar import EdgarClient, SECError, cik_padded
from basin.edgar.client import SEC_DATA_HOST
from basin.store import DEFAULT_DB_PATH, connect

CACHE = Path("data/cache/submissions")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true", help="write to the store")
    parser.add_argument(
        "--fetch", action="store_true",
        help="also fetch the older paginated submission blocks for filings the "
             "cached recent block does not cover",
    )
    parser.add_argument("--cache", type=Path, default=CACHE)
    return parser.parse_args(argv)


def _index(block: dict) -> dict[str, tuple[str | None, str | None]]:
    """``accession -> (reportDate, primaryDocument)`` from one filings block."""
    out: dict[str, tuple[str | None, str | None]] = {}
    accs = block.get("accessionNumber", [])
    reports = block.get("reportDate", [])
    docs = block.get("primaryDocument", [])
    for i, accession in enumerate(accs):
        report = reports[i] if i < len(reports) else None
        doc = docs[i] if i < len(docs) else None
        out[accession] = (report or None, doc or None)
    return out


def known_for(cik: str, *, cache: Path) -> tuple[dict, list[str]]:
    """What the cached payload says, and the older blocks it points at."""
    path = cache / f"CIK{cik_padded(cik)}.json"
    if not path.exists():
        return {}, []
    payload = json.loads(path.read_text())
    filings = payload.get("filings", {}) or {}
    index = _index(filings.get("recent", {}) or {})
    older = [f.get("name") for f in (filings.get("files") or []) if f.get("name")]
    return index, older


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.db)

    pending = [
        dict(r)
        for r in conn.execute(
            "SELECT accession, cik, form FROM filing "
            "WHERE period_end IS NULL OR primary_doc IS NULL"
        )
    ]
    if not pending:
        print("nothing to backfill")
        return 0

    by_cik: dict[str, list[dict]] = collections.defaultdict(list)
    for row in pending:
        by_cik[row["cik"]].append(row)

    print(f"{len(pending):,} filings missing a period or a primary document, "
          f"across {len(by_cik)} filers\n")

    resolved: dict[str, tuple[str | None, str | None]] = {}
    counts: collections.Counter = collections.Counter()
    unresolved: list[dict] = []

    for cik, rows in by_cik.items():
        index, older = known_for(cik, cache=args.cache)
        if not index:
            counts["no cached submissions payload"] += len(rows)
            unresolved.extend(rows)
            continue
        missing = [r for r in rows if r["accession"] not in index]

        if missing and older and args.fetch:
            try:
                with EdgarClient() as client:
                    for name in older:
                        block = client.get_json(f"{SEC_DATA_HOST}/submissions/{name}")
                        index.update(_index(block))
                        counts["older blocks fetched"] += 1
            except SECError as exc:
                print(f"  ! {cik}: {exc}", file=sys.stderr)

        for row in rows:
            found = index.get(row["accession"])
            if found is None:
                counts["not in any block read"] += 1
                unresolved.append(row)
                continue
            report, doc = found
            if report is None and doc is None:
                counts["EDGAR publishes neither field"] += 1
                continue
            resolved[row["accession"]] = found
            counts["period recovered"] += report is not None
            counts["primary document recovered"] += doc is not None

    for key, value in counts.most_common():
        print(f"  {key:<44}{value:>7,}")

    if unresolved:
        forms = collections.Counter(r["form"] for r in unresolved)
        print(f"\n  {len(unresolved):,} unresolved, by form: "
              + ", ".join(f"{f} {n}" for f, n in forms.most_common(6)))
        if not args.fetch:
            print("  pass --fetch to read the older paginated blocks for these")

    if not args.apply:
        print(f"\nreport only; pass --apply to write {len(resolved):,} filings")
        return 0

    # COALESCE, not assignment: a value already present was recorded by a path
    # that had the document in front of it, and is not improved by this one.
    with conn:
        conn.executemany(
            "UPDATE filing SET period_end = COALESCE(period_end, ?), "
            "primary_doc = COALESCE(primary_doc, ?) WHERE accession = ?",
            [(report, doc, accession) for accession, (report, doc) in resolved.items()],
        )

    still = conn.execute(
        "SELECT COUNT(*) FROM filing WHERE period_end IS NULL"
    ).fetchone()[0]
    total = conn.execute("SELECT COUNT(*) FROM filing").fetchone()[0]
    print(f"\napplied: {len(resolved):,} filings updated; "
          f"period_end still NULL on {still:,}/{total:,}")
    print("re-run scripts/index_documents.py --reindex to correct document.kind")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
