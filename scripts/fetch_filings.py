"""Build the document corpus: 10-K, 10-Q and 8-K filings per company.

    export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"
    python scripts/fetch_filings.py --per-company 6

Downloads each filing's primary document — and, for 8-Ks, the EX-99.1 exhibit,
because production and capex guidance is announced in the earnings release
attached to an 8-K rather than in the 8-K itself — and stores the raw HTML
under ``data/corpus/<accession>/``.

Raw HTML is what gets kept, not flattened text: the parse will change, and
re-deriving text from a local file is free while re-fetching is not.
"""

from __future__ import annotations

import argparse
import collections
import json
import sys
from pathlib import Path

from basin.documents import corpus
from basin.documents.locate import earnings_exhibits, filing_dir
from basin.edgar import EdgarClient, NotFound, SECError
from basin.store import DEFAULT_DB_PATH, connect, record_filing

SUBMISSIONS = Path("data/cache/submissions")
FORMS = ("10-K", "10-Q", "8-K")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--per-company", type=int, default=6,
                        help="most recent filings to take of each form")
    parser.add_argument("--forms", default=",".join(FORMS))
    parser.add_argument("--since", default="2023-01-01")
    parser.add_argument("--exhibits", action="store_true", default=True,
                        help="also fetch EX-99.1 from 8-Ks (guidance lives there)")
    return parser.parse_args(argv)


def wanted_filings(payload: dict, forms: tuple[str, ...], since: str, per_form: int):
    """Most recent filings of each form, newest first."""
    recent = payload.get("filings", {}).get("recent", {})
    rows = list(zip(
        recent.get("form", []),
        recent.get("filingDate", []),
        recent.get("accessionNumber", []),
        recent.get("primaryDocument", []),
        recent.get("reportDate", []),
    ))
    picked: dict[str, list] = collections.defaultdict(list)
    for form, filed, accession, document, period in sorted(rows, key=lambda r: r[1], reverse=True):
        base = form.split("/")[0]
        if base not in forms or filed < since or not document:
            continue
        if len(picked[base]) >= per_form:
            continue
        picked[base].append((form, filed, accession, document, period))
    return [f for group in picked.values() for f in group]


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    forms = tuple(args.forms.split(","))
    conn = connect(args.store)
    ciks = [r[0] for r in conn.execute("SELECT cik FROM company ORDER BY cik")]

    counts: collections.Counter = collections.Counter()
    try:
        with EdgarClient() as client:
            for n, cik in enumerate(ciks, 1):
                cached = SUBMISSIONS / f"CIK{cik}.json"
                if not cached.exists():
                    counts["no submissions"] += 1
                    continue
                payload = json.loads(cached.read_text())

                for form, filed, accession, document, period in wanted_filings(
                    payload, forms, args.since, args.per_company
                ):
                    record_filing(conn, accession, cik, form, filed,
                                  period_end=period or None, primary_doc=document)
                    targets = [document]
                    if args.exhibits and form.startswith("8-K"):
                        targets += earnings_exhibits(client, cik, accession)

                    for name in targets:
                        if corpus.is_stored(accession, name):
                            counts["cached"] += 1
                            continue
                        try:
                            raw = client.get_text(f"{filing_dir(cik, accession)}/{name}")
                        except (NotFound, SECError):
                            counts["missing"] += 1
                            continue
                        corpus.store(accession, name, raw)
                        counts[form.split("/")[0]] += 1
                conn.commit()
                if n % 10 == 0:
                    print(f"  {n}/{len(ciks)} companies  " +
                          ", ".join(f"{k} {v}" for k, v in counts.most_common()), flush=True)
    except SECError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.commit()
        conn.close()

    print("\n" + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
