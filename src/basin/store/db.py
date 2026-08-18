"""Fact store access. Thin by design — the schema does the enforcing.

The store is append-only: there is no update path for a fact, and callers get
an insert that is idempotent per accession rather than an upsert that would
overwrite history.
"""

from __future__ import annotations

import sqlite3
from importlib import resources
from pathlib import Path
from typing import Iterable

from basin.facts.xbrl import CompanyCoverage, FactRow

DEFAULT_DB_PATH = Path("data/basin.db")


def schema_sql() -> str:
    return resources.files("basin.store").joinpath("schema.sql").read_text()


def connect(path: Path | str = DEFAULT_DB_PATH, *, create: bool = True) -> sqlite3.Connection:
    """Open the store, applying the schema if it is not there yet."""
    path = Path(path)
    if create:
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    if create:
        conn.executescript(schema_sql())
        conn.commit()
    return conn


def upsert_company(
    conn: sqlite3.Connection,
    cik: str,
    name: str,
    *,
    ticker: str | None = None,
    basin: str | None = None,
    is_operator: bool = True,
    notes: str | None = None,
) -> None:
    """Insert a cohort member, refreshing its descriptive fields if present.

    Companies are metadata, not facts — updating a ticker rewrites no history.
    """
    conn.execute(
        """
        INSERT INTO company (cik, ticker, name, basin, is_operator, notes)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(cik) DO UPDATE SET
            ticker = excluded.ticker,
            name = excluded.name,
            basin = excluded.basin,
            is_operator = excluded.is_operator,
            notes = excluded.notes
        """,
        (cik, ticker, name, basin, int(is_operator), notes),
    )


def record_filing(
    conn: sqlite3.Connection,
    accession: str,
    cik: str,
    form: str,
    filed_date: str,
    *,
    period_end: str | None = None,
    primary_doc: str | None = None,
) -> None:
    """Register a filing so facts referencing it can be cited."""
    conn.execute(
        """
        INSERT INTO filing (accession, cik, form, filed_date, period_end, primary_doc)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(accession) DO NOTHING
        """,
        (accession, cik, form, filed_date, period_end, primary_doc),
    )


def insert_facts(conn: sqlite3.Connection, rows: Iterable[FactRow]) -> int:
    """Append fact rows, skipping ones already stored for the same accession.

    Returns the number newly written. Filings referenced by the rows must be
    registered first — the foreign key is there to make a fact with no citable
    document impossible, so a missing filing is meant to fail loudly.
    """
    written = 0
    for row in rows:
        cursor = conn.execute(
            """
            INSERT INTO fact (
                cik, concept_key, value, unit, product, unit_rank,
                period_start, period_end, fiscal_year, fiscal_period,
                accession, form, extracted_by, taxonomy, tag
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT DO NOTHING
            """,
            (
                row.cik,
                row.concept_key,
                row.value,
                row.unit,
                row.product,
                row.unit_rank,
                row.period_start,
                row.period_end,
                row.fiscal_year,
                row.fiscal_period,
                row.accession,
                row.form,
                row.extracted_by,
                row.taxonomy,
                row.tag,
            ),
        )
        written += cursor.rowcount if cursor.rowcount > 0 else 0
    return written


def record_coverage(conn: sqlite3.Connection, coverage: CompanyCoverage) -> None:
    """Store one coverage measurement, so the gap is tracked over time."""
    conn.executemany(
        """
        INSERT INTO coverage_snapshot
            (cik, concept_key, tagged, taxonomy, tag, observations, latest_period)
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        [
            (
                coverage.cik,
                c.concept_key,
                int(c.tagged),
                c.taxonomy,
                c.tag,
                c.observation_count,
                c.latest_period_end,
            )
            for c in coverage.concepts
        ],
    )


def record_alias_validation(
    conn: sqlite3.Connection, validation, family: str = "reserves"
) -> None:
    """Store what the alias validator decided for one filer.

    Replaces the previous verdict rather than appending: this is a statement
    about the current registry and payload, not a historical fact about a
    filing, so it carries no citation and nothing depends on its history.
    """
    if validation.choices:
        rows = [
            (
                validation.cik, family, key, choice.taxonomy, choice.tag, choice.unit,
                validation.status, validation.coherent_periods,
                validation.tested_periods, validation.median_error, validation.note,
            )
            for key, choice in validation.choices.items()
        ]
    else:
        rows = [
            (
                validation.cik, family, "*", None, None, None,
                validation.status, 0, 0, None, validation.note,
            )
        ]
    conn.executemany(
        """
        INSERT INTO alias_validation
            (cik, family, concept_key, taxonomy, tag, unit, status,
             coherent_periods, tested_periods, median_error, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cik, family, concept_key) DO UPDATE SET
            taxonomy = excluded.taxonomy, tag = excluded.tag, unit = excluded.unit,
            status = excluded.status, coherent_periods = excluded.coherent_periods,
            tested_periods = excluded.tested_periods,
            median_error = excluded.median_error, note = excluded.note,
            checked_at = datetime('now')
        """,
        rows,
    )
