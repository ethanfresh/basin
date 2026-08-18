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
        _add_missing_columns(conn)
        conn.commit()
    return conn


# Columns added after a store was first created. `CREATE TABLE IF NOT EXISTS`
# leaves an existing table alone, so new columns need adding explicitly rather
# than by rebuilding — the fact rows are expensive to re-fetch.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "fact_verification": {
        "page": "INTEGER",
        "line_no": "INTEGER",
        "char_offset": "INTEGER",
        "section": "TEXT",
        "line_text": "TEXT",
        "units_nearby": "TEXT",
    },
}


def _add_missing_columns(conn: sqlite3.Connection) -> None:
    for table, columns in _ADDED_COLUMNS.items():
        existing = {r[1] for r in conn.execute(f"PRAGMA table_info({table})")}
        if not existing:
            continue
        for name, decl in columns.items():
            if name not in existing:
                conn.execute(f"ALTER TABLE {table} ADD COLUMN {name} {decl}")


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


def record_verification(
    conn: sqlite3.Connection,
    fact_id: int,
    status: str,
    *,
    document: str | None = None,
    printed: str | None = None,
    scale_found: float | None = None,
    scale_label: str | None = None,
    hits: int | None = None,
    source_span: str | None = None,
    char_offset: int | None = None,
    line_no: int | None = None,
    section: str | None = None,
    units_nearby: str | None = None,
    page: int | None = None,
    line_text: str | None = None,
    note: str | None = None,
) -> None:
    """Record the outcome of checking one fact against its filing."""
    conn.execute(
        """
        INSERT INTO fact_verification
            (fact_id, status, document, printed, scale_found, scale_label,
             hits, source_span, char_offset, line_no, section, units_nearby,
             page, line_text, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fact_id) DO UPDATE SET
            status = excluded.status, document = excluded.document,
            printed = excluded.printed, scale_found = excluded.scale_found,
            scale_label = excluded.scale_label, hits = excluded.hits,
            source_span = excluded.source_span,
            char_offset = excluded.char_offset, line_no = excluded.line_no,
            section = excluded.section, units_nearby = excluded.units_nearby,
            page = excluded.page, line_text = excluded.line_text,
            note = excluded.note, checked_at = datetime('now')
        """,
        (fact_id, status, document, printed, scale_found, scale_label,
         hits, source_span, char_offset, line_no, section, units_nearby,
         page, line_text, note),
    )


def record_scale(
    conn: sqlite3.Connection,
    fact_id: int,
    divisor: float,
    canonical_value: float,
    canonical_unit: str,
    basis: str,
    *,
    conversion_note: str | None = None,
    usd_per_boe: float | None = None,
    rejected: str | None = None,
    note: str | None = None,
) -> None:
    """Record the resolved magnitude of one fact, with its evidence."""
    conn.execute(
        """
        INSERT INTO fact_scale (fact_id, divisor, canonical_value, canonical_unit,
                                conversion_note, basis, usd_per_boe, rejected, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fact_id) DO UPDATE SET
            divisor = excluded.divisor,
            canonical_value = excluded.canonical_value,
            canonical_unit = excluded.canonical_unit,
            conversion_note = excluded.conversion_note,
            basis = excluded.basis, usd_per_boe = excluded.usd_per_boe,
            rejected = excluded.rejected, note = excluded.note,
            resolved_at = datetime('now')
        """,
        (fact_id, divisor, canonical_value, canonical_unit, conversion_note,
         basis, usd_per_boe, rejected, note),
    )
