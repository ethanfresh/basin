"""Check stored facts against the filings they cite.

    export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"
    python scripts/verify_facts.py --limit 200

Fetches each cited filing's primary document once, caches it, and searches for
every value that filing is supposed to support. Records whether the figure was
found and at what scale, because the document is the only place a filing's
presentation scale is actually stated.

Documents are large — a 10-K runs to several megabytes — so work is grouped by
accession and the cache is worth keeping.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

from basin.documents import document_url, find_value, html_to_text, primary_document
from basin.edgar import EdgarClient, NotFound, SECError
from basin.store import DEFAULT_DB_PATH, connect, record_verification

DOC_CACHE = Path("data/cache/documents")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=200, help="facts to check")
    parser.add_argument("--concept", action="append", default=[])
    parser.add_argument("--since", default="2023-01-01", help="minimum period_end")
    parser.add_argument("--recheck", action="store_true")
    return parser.parse_args(argv)


def load_text(client: EdgarClient, cik: str, accession: str) -> tuple[str, str] | None:
    """Fetch and cache the filing's primary document as flattened text."""
    cached = DOC_CACHE / f"{accession}.txt"
    name_file = DOC_CACHE / f"{accession}.name"
    if cached.exists() and name_file.exists():
        return name_file.read_text().strip(), cached.read_text()

    document = primary_document(cik, accession, client=client)
    if not document:
        return None
    raw = client.get_text(document_url(cik, accession, document))
    text = html_to_text(raw)

    DOC_CACHE.mkdir(parents=True, exist_ok=True)
    cached.write_text(text)
    name_file.write_text(document)
    return document, text


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)

    sql = """
        SELECT f.id, f.cik, f.concept_key, f.value, f.unit, f.period_end, f.accession
        FROM fact_current f
        WHERE f.period_end >= ?
    """
    params: list = [args.since]
    if args.concept:
        sql += f" AND f.concept_key IN ({','.join('?' * len(args.concept))})"
        params += args.concept
    if not args.recheck:
        sql += " AND f.id NOT IN (SELECT fact_id FROM fact_verification)"
    # Grouped by accession so each document is fetched once.
    sql += " ORDER BY f.accession, f.concept_key LIMIT ?"
    params.append(args.limit)

    facts = [dict(r) for r in conn.execute(sql, params)]
    if not facts:
        print("nothing to verify")
        return 0

    by_accession: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for fact in facts:
        by_accession[(fact["cik"], fact["accession"])].append(fact)

    counts: collections.Counter = collections.Counter()
    scales: collections.Counter = collections.Counter()

    try:
        with EdgarClient() as client:
            for n, ((cik, accession), group) in enumerate(sorted(by_accession.items()), 1):
                try:
                    loaded = load_text(client, cik, accession)
                except (NotFound, SECError) as exc:
                    for fact in group:
                        record_verification(conn, fact["id"], "unavailable", note=str(exc)[:200])
                        counts["unavailable"] += 1
                    continue

                if loaded is None:
                    for fact in group:
                        record_verification(
                            conn, fact["id"], "unavailable", note="no primary document"
                        )
                        counts["unavailable"] += 1
                    continue

                document, text = loaded
                for fact in group:
                    match = find_value(text, fact["value"])
                    if match is None:
                        record_verification(
                            conn, fact["id"], "not_found", document=document
                        )
                        counts["not_found"] += 1
                        continue
                    record_verification(
                        conn,
                        fact["id"],
                        "found",
                        document=document,
                        printed=match.printed,
                        scale_found=match.scale,
                        scale_label=match.scale_label,
                        hits=match.hits,
                        source_span=match.source_span,
                    )
                    counts["found"] += 1
                    scales[match.scale_label] += 1
                conn.commit()
                print(f"  [{n}/{len(by_accession)}] {accession}  {len(group)} facts", flush=True)
    except SECError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.commit()
        conn.close()

    total = sum(counts.values())
    print(f"\n{'=' * 56}\nverified {total} facts across {len(by_accession)} filings")
    for status, n in counts.most_common():
        print(f"  {status:<14}{n:>5}  {n / total:>5.0%}")
    if scales:
        print("\nscale the document printed the figure at:")
        for label, n in scales.most_common():
            print(f"  {label:<40}{n:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
