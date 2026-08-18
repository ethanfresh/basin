"""Parse the stored corpus into the database, one row per line.

    python scripts/index_documents.py

Reads raw HTML from ``data/corpus``, flattens it with page and line
coordinates, and writes it to ``document`` / ``document_line`` plus a
full-text index. Nothing is fetched: the corpus on disk is the input, so this
can be re-run whenever the parser improves.
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.documents import corpus
from basin.documents.text import parse, section_at, section_index
from basin.store import DEFAULT_DB_PATH, connect


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--corpus", type=Path, default=corpus.CORPUS)
    parser.add_argument("--limit", type=int, default=0, help="0 = every document")
    parser.add_argument("--reindex", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)

    filings = {
        r["accession"]: dict(r)
        for r in conn.execute(
            "SELECT accession, cik, form, filed_date, primary_doc FROM filing"
        )
    }
    done = {
        (r[0], r[1])
        for r in conn.execute("SELECT accession, name FROM document")
    } if not args.reindex else set()

    accessions = sorted(p.name for p in args.corpus.iterdir() if p.is_dir())
    counts: collections.Counter = collections.Counter()
    total_lines = 0

    for n, accession in enumerate(accessions, 1):
        meta = filings.get(accession, {})
        for stored in corpus.stored_documents(accession, root=args.corpus):
            if not stored.name.lower().endswith((".htm", ".html")):
                continue
            if (accession, stored.name) in done:
                counts["already indexed"] += 1
                continue

            document = parse(stored.path.read_text(errors="replace"))
            kind = "primary" if stored.name == meta.get("primary_doc") else "exhibit"

            cursor = conn.execute(
                """
                INSERT INTO document
                    (accession, name, cik, form, filed_date, kind,
                     pages, line_count, char_count)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                ON CONFLICT(accession, name) DO UPDATE SET
                    pages = excluded.pages, line_count = excluded.line_count,
                    char_count = excluded.char_count, kind = excluded.kind,
                    indexed_at = datetime('now')
                RETURNING id
                """,
                (accession, stored.name, meta.get("cik"), meta.get("form"),
                 meta.get("filed_date"), kind, document.pages,
                 len(document.lines), len(document.text)),
            )
            document_id = cursor.fetchone()[0]
            conn.execute("DELETE FROM document_line WHERE document_id = ?", (document_id,))

            # Headings are indexed once and then looked up by binary search.
            # Calling section_of per line rescans the document each time, which
            # is quadratic and turned a 20-document run into minutes.
            headings = section_index(document.text)
            rows = [
                (
                    document_id, line.line, line.page,
                    section_at(headings, line.start), line.start, line.text,
                )
                for line in document.lines
            ]
            conn.executemany(
                """INSERT INTO document_line
                       (document_id, line_no, page, section, char_offset, text)
                   VALUES (?, ?, ?, ?, ?, ?)""",
                rows,
            )
            counts[kind] += 1
            total_lines += len(rows)

        if n % 100 == 0:
            conn.commit()
            print(f"  {n}/{len(accessions)} accessions  "
                  + ", ".join(f"{k} {v}" for k, v in counts.most_common())
                  + f", {total_lines:,} lines", flush=True)
        if args.limit and n >= args.limit:
            break

    conn.commit()
    print("rebuilding the full-text index …")
    conn.execute("INSERT INTO document_search(document_search) VALUES('rebuild')")
    conn.commit()

    print("\n" + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    print(f"lines indexed: {total_lines:,}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
