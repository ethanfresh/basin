"""Build the store the dashboard serves, without the document index.

    python scripts/build_serving_store.py

Writes ``build/serving.db``: the facts, citations and verification rows the
dashboard reads, without the ~3GB parsed corpus that only ingestion touches.
The result ships inside the container image, so there is no volume to keep in
sync — see docs/deploy.md.
"""

from __future__ import annotations

import argparse
from pathlib import Path

from basin.store import DEFAULT_DB_PATH
from basin.store.serving import build


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    # build/, not data/: this is a deployment artifact that has to sit inside
    # the Docker build context, and data/ is excluded from it (and is normally
    # a symlink to a store kept off the checkout entirely).
    parser.add_argument("--out", type=Path, default=Path("build/serving.db"))
    parser.add_argument("--force", action="store_true", help="overwrite --out")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    if not args.store.exists():
        print(f"no store at {args.store}")
        return 1
    if args.out.exists():
        if not args.force:
            print(f"{args.out} exists; pass --force to replace it")
            return 1
        args.out.unlink()

    try:
        counts = build(args.store, args.out)
    except ValueError as exc:
        print(exc)
        return 1

    for table, rows in counts.items():
        print(f"{table:<24} {rows:>10,}")

    source_mb = args.store.stat().st_size / 1048576
    out_mb = args.out.stat().st_size / 1048576
    print(f"\n{args.out}  {out_mb:,.1f}MB  (from {source_mb:,.1f}MB)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
