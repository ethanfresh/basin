"""Build the store the dashboard serves, without the document index.

The working store is two databases wearing one filename. ``document_line`` and
its full-text index are the parsed corpus — ~3GB, the input to verification and
table extraction, read only by scripts on a workstation. The facts they
produce, with every citation the dashboard renders, are ~15MB.
:mod:`basin.store.queries` — the only module the web app talks to — names
neither document table.

Shipping the difference is what lets the serving store ride inside the
container image instead of on a volume: nothing pins the app to one machine in
one region, and a deploy is the whole update mechanism. See docs/deploy.md.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from basin.store.db import connect

# The index itself. `document` stays: it is a few thousand rows cataloguing
# what was indexed, and fact_verification cites documents by name.
EXCLUDED = frozenset({"document_line", "document_search"})


def copyable_tables(dest: sqlite3.Connection) -> list[str]:
    """Real tables in the shipped schema, minus the index and FTS internals.

    Driven off the destination rather than a hardcoded list, so a table added
    to schema.sql is carried without this module being edited — the failure
    mode of a list is a table silently missing from the shipped store.

    FTS5 shadow tables (``document_search_data`` and its siblings) are
    maintained by the virtual table and must never be written directly.
    """
    return [
        name
        for (name,) in dest.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table' ORDER BY name"
        )
        if not name.startswith("sqlite_")
        and name not in EXCLUDED
        and not any(name.startswith(f"{excluded}_") for excluded in EXCLUDED)
    ]


def _copy(dest: sqlite3.Connection, table: str) -> int:
    """Copy one table from the attached source, by column name.

    By name, not ``SELECT *``: a column added by the migration in
    :func:`basin.store.db.connect` lands at the end of an existing table but in
    schema.sql's declared position on a fresh one, so a positional copy would
    silently transpose values between two stores that are both current.

    Columns the source lacks are skipped rather than failing, so a store
    predating a schema addition still ships.
    """
    wanted = [r[1] for r in dest.execute(f"PRAGMA table_info({table})")]
    present = {r[1] for r in dest.execute(f"PRAGMA src.table_info({table})")}
    columns = [c for c in wanted if c in present]
    if not columns:
        return 0
    names = ", ".join(f'"{c}"' for c in columns)
    dest.execute(f"INSERT INTO main.{table} ({names}) SELECT {names} FROM src.{table}")
    return dest.execute(f"SELECT COUNT(*) FROM main.{table}").fetchone()[0]


def build(source: Path | str, out: Path | str) -> dict[str, int]:
    """Write a facts-only copy of ``source`` to ``out``, returning row counts.

    The output carries the full schema, so the omitted tables are present and
    empty rather than missing — a query that reaches for them on the host
    returns no rows instead of raising.

    Reads the source and writes a new file rather than copying one, so this is
    safe to run while an ingest holds a transaction: ``cp`` of a live database
    captures a hot journal that a ``mode=ro`` reader cannot roll back.

    Raises ``ValueError`` if the result would violate a foreign key, which is
    the one way a partial copy could reach a host looking whole.
    """
    source, out = Path(source), Path(out)
    out.parent.mkdir(parents=True, exist_ok=True)

    # connect() applies schema.sql, so the output carries the current tables,
    # indexes and views whatever state the source was left in.
    dest = connect(out)
    try:
        # Off for the copy: the source already satisfies every constraint, and
        # enforcing them here would impose an insertion order on tables that
        # reference each other.
        dest.execute("PRAGMA foreign_keys = OFF")
        # A plain path, not a mode=ro URI: URI parsing is enabled per
        # connection at open time and connect() does not open with it, so
        # ATTACH would take the URI for a filename. Nothing here writes to src.
        dest.execute("ATTACH DATABASE ? AS src", (str(source),))

        counts = {table: _copy(dest, table) for table in copyable_tables(dest)}
        dest.commit()

        violations = dest.execute("PRAGMA foreign_key_check").fetchall()
        if violations:
            raise ValueError(f"{len(violations)} foreign key violations; not shipping")

        dest.execute("DETACH DATABASE src")
        dest.execute("VACUUM")
    finally:
        dest.close()
    return counts
