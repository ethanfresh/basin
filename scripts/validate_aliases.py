"""Re-run per-filer alias validation from cached payloads.

    python scripts/validate_aliases.py

Recomputes the verdict without re-reading facts, for when the validation logic
changes but the ingested rows do not. Reads ``data/cache/companyfacts``.
"""

from __future__ import annotations

import argparse
import collections
import json
from pathlib import Path

from basin.facts.validation import validate_reserve_family
from basin.store import DEFAULT_DB_PATH, connect, record_alias_validation

CACHE = Path("data/cache/companyfacts")


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--cache", type=Path, default=CACHE)
    args = parser.parse_args(argv)

    conn = connect(args.store)
    known = {r[0] for r in conn.execute("SELECT cik FROM company")}
    counts: collections.Counter = collections.Counter()

    for path in sorted(args.cache.glob("CIK*.json")):
        payload = json.loads(path.read_text())
        validation = validate_reserve_family(payload)
        if validation.cik not in known:
            continue
        record_alias_validation(conn, validation)
        counts[validation.status] += 1

    conn.commit()
    for status, n in counts.most_common():
        print(f"  {status:<14}{n:>4} filers")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
