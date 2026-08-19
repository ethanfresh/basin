"""Re-derive the "Item N." section of every located citation.

    python scripts/resection_facts.py [--dry-run]

The section is a function of the document and the character offset already
stored by verification, so it can be recomputed from the corpus on disk
without fetching anything or re-running the search. `verify_facts.py --recheck`
would also rewrite it, but it re-verifies every fact against EDGAR to change
one derived column.

Why it needs re-deriving (D9): `section_of` took the last Item heading before
the offset, and a 10-K lists every item on its contents page, so anything
printed before the first real heading was filed under the *last* item in the
contents -- EQT's proved reserves summary, on page 11, read "Item 16. Form
10-K Summary". 987 of 9,455 stored citations named the wrong section.
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
    parser.add_argument("--dry-run", action="store_true",
                        help="report what would change, write nothing")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)

    rows = conn.execute(
        """SELECT v.fact_id, v.document, v.char_offset, v.section, f.accession
             FROM fact_verification v JOIN fact f ON f.id = v.fact_id
            WHERE v.char_offset IS NOT NULL AND v.document IS NOT NULL"""
    ).fetchall()

    # Grouped by document, because parsing a 10-K is the expensive part and
    # every citation into it shares one heading index.
    by_document: dict[tuple[str, str], list] = collections.defaultdict(list)
    for row in rows:
        by_document[(row["accession"], row["document"])].append(row)

    counts: collections.Counter = collections.Counter()
    updates: list[tuple[str | None, int]] = []
    for n, ((accession, name), group) in enumerate(sorted(by_document.items()), 1):
        raw = corpus.load_raw(accession, name, root=args.corpus)
        if raw is None:
            counts["document missing from the corpus"] += len(group)
            continue
        headings = section_index(parse(raw).text)
        for row in group:
            section = section_at(headings, row["char_offset"])
            if section == row["section"]:
                counts["unchanged"] += 1
                continue
            counts["no section" if section is None else "resectioned"] += 1
            updates.append((section, row["fact_id"]))
        if n % 200 == 0:
            print(f"  {n}/{len(by_document)} documents", flush=True)

    if not args.dry_run:
        conn.executemany(
            "UPDATE fact_verification SET section = ? WHERE fact_id = ?", updates
        )
        conn.commit()

    print(f"{len(rows):,} located citations over {len(by_document):,} documents")
    for label, count in counts.most_common():
        print(f"  {count:6,}  {label}")
    if args.dry_run:
        print("(dry run: nothing written)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
