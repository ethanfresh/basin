"""Fact store access. Thin by design — the schema does the enforcing.

The store is append-only: there is no update path for a fact, and callers get
an insert that is idempotent per accession rather than an upsert that would
overwrite history.
"""

from __future__ import annotations

import os
import sqlite3
from importlib import resources
from pathlib import Path
from typing import Iterable
from urllib.parse import quote

from basin.facts.xbrl import CompanyCoverage, FactRow

# Read at import so the scripts, which capture this as an argparse default, and
# the web app, which uses it as a fallback, both pick up a deployment's location
# without either being edited. A host that mounts a volume elsewhere sets
# BASIN_DB; an explicit --store still wins over both.
DEFAULT_DB_PATH = Path(os.environ.get("BASIN_DB") or "data/basin.db")


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
    # Wait for a writer rather than failing the moment one holds the lock.
    #
    # Several processes reach this store at once by design -- the dashboard
    # reads it while an ingest or verification pass writes, and the indexing
    # script runs for minutes at a time. Without this, SQLite raises "database
    # is locked" immediately and a pass that was most of the way through a
    # thousand filings dies on someone else's transaction.
    conn.execute("PRAGMA busy_timeout = 30000")
    if create:
        # Columns first: schema.sql indexes columns that a store created before
        # they existed does not have yet, and an index on a missing column is a
        # hard error. On a fresh store there is no table to alter, and the
        # migration skips it -- so this order is correct in both directions.
        _add_missing_columns(conn)
        _drop_views(conn)
        conn.executescript(schema_sql())
        conn.commit()
    return conn


def connect_readonly(path: Path | str = DEFAULT_DB_PATH) -> sqlite3.Connection:
    """Open the store read-only, applying no schema.

    ``mode=ro`` makes "this caller cannot write" a property of the connection
    rather than a convention, and skipping the schema keeps a reader off the
    write path entirely -- a served copy of the store stays byte-identical to
    the one that was shipped.

    The path is percent-encoded because SQLite parses everything after ``?`` in
    a URI as parameters, so an unescaped ``?`` in a directory name would
    silently truncate the filename and open the wrong database.
    """
    conn = sqlite3.connect(f"file:{quote(str(path))}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    # The store is far larger than the row set any one request touches, so let
    # the OS page in what is read instead of copying through SQLite's cache.
    conn.execute("PRAGMA mmap_size = 268435456")
    return conn


# Columns added after a store was first created. `CREATE TABLE IF NOT EXISTS`
# leaves an existing table alone, so new columns need adding explicitly rather
# than by rebuilding — the fact rows are expensive to re-fetch.
_ADDED_COLUMNS: dict[str, dict[str, str]] = {
    "company": {
        "cohort": "TEXT",
        "cohort_source": "TEXT",
        "cohort_as_of": "TEXT",
        "country": "TEXT",
        "market_cap_musd": "REAL",
        "listing_status": "TEXT",
        "last_filing_date": "TEXT",
        "listing_note": "TEXT",
        "reporting_taxonomy": "TEXT",
        "taxonomy_note": "TEXT",
        "disclosure_regime": "TEXT",
        "regime_note": "TEXT",
    },
    "fact_verification": {
        "page": "INTEGER",
        "line_no": "INTEGER",
        "char_offset": "INTEGER",
        "section": "TEXT",
        "line_text": "TEXT",
        "units_nearby": "TEXT",
        "method": "TEXT",
        "anchor": "TEXT",
        "folio": "INTEGER",
        "scale_declared": "INTEGER",
    },
}


def _drop_views(conn: sqlite3.Connection) -> None:
    """Drop every view, so schema.sql's definitions are the ones in force.

    The views are declared CREATE VIEW IF NOT EXISTS, which reads as "make sure
    this exists" and means "keep whatever is already there". A view holds no
    data, so its definition in the file is the only copy that matters -- but a
    store created before an edit kept the old text forever, and the correction
    reached new databases only. Editing reserve_consistency and seeing nothing
    change is how that surfaced. They are cheap to rebuild, so they are.
    """
    views = [
        r[0] for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'"
        )
    ]
    for name in views:
        conn.execute(f'DROP VIEW IF EXISTS "{name}"')


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
    is_operator: bool | None = None,
    cohort: str | None = None,
    cohort_source: str | None = None,
    cohort_as_of: str | None = None,
    country: str | None = None,
    market_cap_musd: float | None = None,
    reporting_taxonomy: str | None = None,
    taxonomy_note: str | None = None,
    disclosure_regime: str | None = None,
    regime_note: str | None = None,
    notes: str | None = None,
) -> None:
    """Insert a cohort member, refreshing its descriptive fields if present.

    Companies are metadata, not facts — updating a ticker rewrites no history.

    Descriptive fields are only overwritten when a value is supplied, so a
    caller that knows about tickers does not blank out a cohort assigned by a
    caller that knows about cohorts. COALESCE, not assignment.
    """
    conn.execute(
        """
        INSERT INTO company (cik, ticker, name, basin, is_operator,
                             cohort, cohort_source, cohort_as_of,
                             country, market_cap_musd,
                             reporting_taxonomy, taxonomy_note,
                             disclosure_regime, regime_note, notes)
        VALUES (:cik, :ticker, :name, :basin, COALESCE(:is_operator, 1),
                :cohort, :cohort_source, :cohort_as_of,
                :country, :market_cap_musd,
                :reporting_taxonomy, :taxonomy_note,
                :disclosure_regime, :regime_note, :notes)
        ON CONFLICT(cik) DO UPDATE SET
            ticker = COALESCE(:ticker, ticker),
            name = excluded.name,
            basin = COALESCE(:basin, basin),
            is_operator = COALESCE(:is_operator, is_operator),
            cohort = COALESCE(:cohort, cohort),
            cohort_source = COALESCE(:cohort_source, cohort_source),
            cohort_as_of = COALESCE(:cohort_as_of, cohort_as_of),
            country = COALESCE(:country, country),
            market_cap_musd = COALESCE(:market_cap_musd, market_cap_musd),
            reporting_taxonomy = COALESCE(:reporting_taxonomy, reporting_taxonomy),
            taxonomy_note = COALESCE(:taxonomy_note, taxonomy_note),
            disclosure_regime = COALESCE(:disclosure_regime, disclosure_regime),
            regime_note = COALESCE(:regime_note, regime_note),
            notes = COALESCE(:notes, notes)
        """,
        {
            "cik": cik,
            "ticker": ticker,
            "name": name,
            "basin": basin,
            "is_operator": None if is_operator is None else int(is_operator),
            "cohort": cohort,
            "cohort_source": cohort_source,
            "cohort_as_of": cohort_as_of,
            "country": country,
            "market_cap_musd": market_cap_musd,
            "reporting_taxonomy": reporting_taxonomy,
            "taxonomy_note": taxonomy_note,
            "disclosure_regime": disclosure_regime,
            "regime_note": regime_note,
            "notes": notes,
        },
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
    """Register a filing so facts referencing it can be cited.

    Only fills blanks. Two writers reach this table with different knowledge:
    ingest_xbrl sees an accession on a fact and knows nothing about the
    document, while fetch_filings reads the submissions index and knows the
    primary document's filename. Whichever arrives second used to be discarded,
    which left every filing ingested from facts with a NULL primary_doc
    permanently. COALESCE lets the better-informed writer complete the row
    without any writer being able to overwrite what is already known.
    """
    conn.execute(
        """
        INSERT INTO filing (accession, cik, form, filed_date, period_end, primary_doc)
        VALUES (?, ?, ?, ?, ?, ?)
        ON CONFLICT(accession) DO UPDATE SET
            period_end = COALESCE(period_end, excluded.period_end),
            primary_doc = COALESCE(primary_doc, excluded.primary_doc)
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
                accession, form, extracted_by, taxonomy, tag,
                source_span, section, is_hedged
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
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
                row.source_span,
                row.section,
                None if row.is_hedged is None else int(row.is_hedged),
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
                validation.tested_periods, validation.median_error,
                ",".join(sorted(validation.incoherent_period_ends)) or None,
                validation.note,
            )
            for key, choice in validation.choices.items()
        ]
    else:
        rows = [
            (
                validation.cik, family, "*", None, None, None,
                validation.status, 0, 0, None, None, validation.note,
            )
        ]
    conn.executemany(
        """
        INSERT INTO alias_validation
            (cik, family, concept_key, taxonomy, tag, unit, status,
             coherent_periods, tested_periods, median_error,
             incoherent_periods, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cik, family, concept_key) DO UPDATE SET
            taxonomy = excluded.taxonomy, tag = excluded.tag, unit = excluded.unit,
            status = excluded.status, coherent_periods = excluded.coherent_periods,
            tested_periods = excluded.tested_periods,
            median_error = excluded.median_error,
            incoherent_periods = excluded.incoherent_periods,
            note = excluded.note,
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
    method: str | None = None,
    anchor: str | None = None,
    folio: int | None = None,
    scale_declared: int | None = None,
    note: str | None = None,
) -> None:
    """Record the outcome of checking one fact against its filing."""
    conn.execute(
        """
        INSERT INTO fact_verification
            (fact_id, status, document, printed, scale_found, scale_label,
             hits, source_span, char_offset, line_no, section, units_nearby,
             page, line_text, method, anchor, folio, scale_declared, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(fact_id) DO UPDATE SET
            status = excluded.status, document = excluded.document,
            printed = excluded.printed, scale_found = excluded.scale_found,
            scale_label = excluded.scale_label, hits = excluded.hits,
            source_span = excluded.source_span,
            char_offset = excluded.char_offset, line_no = excluded.line_no,
            section = excluded.section, units_nearby = excluded.units_nearby,
            page = excluded.page, line_text = excluded.line_text,
            method = excluded.method, anchor = excluded.anchor,
            folio = excluded.folio, scale_declared = excluded.scale_declared,
            note = excluded.note, checked_at = datetime('now')
        """,
        (fact_id, status, document, printed, scale_found, scale_label,
         hits, source_span, char_offset, line_no, section, units_nearby,
         page, line_text, method, anchor, folio, scale_declared, note),
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


def record_succession(conn: sqlite3.Connection, succession) -> None:
    """Record one registrant superseding another, with its evidence.

    Replaces rather than appends: this is a statement about the current state of
    EDGAR's registrant graph, not a historical fact about a filing, so re-running
    the resolver should correct a previous verdict rather than accumulate them.
    """
    conn.execute(
        """
        INSERT INTO registrant_succession
            (successor_cik, successor_name, predecessor_cik, predecessor_name,
             accession, filed_date, status, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(successor_cik) DO UPDATE SET
            successor_name = excluded.successor_name,
            predecessor_cik = excluded.predecessor_cik,
            predecessor_name = excluded.predecessor_name,
            accession = excluded.accession,
            filed_date = excluded.filed_date,
            status = excluded.status,
            note = excluded.note,
            resolved_at = datetime('now')
        """,
        (succession.successor_cik, succession.successor_name,
         succession.predecessor_cik, succession.predecessor_name,
         succession.accession, succession.filed_date, succession.status,
         succession.note),
    )


def record_producer_check(conn: sqlite3.Connection, check, cohort: str | None = None) -> None:
    """Store whether a cohort member produces hydrocarbons, with its evidence."""
    conn.execute(
        """
        INSERT INTO producer_check
            (cik, cohort, verdict, concepts, phrase_hits, document,
             documents_read, note)
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(cik) DO UPDATE SET
            cohort = excluded.cohort,
            verdict = excluded.verdict,
            concepts = excluded.concepts,
            phrase_hits = excluded.phrase_hits,
            document = excluded.document,
            documents_read = excluded.documents_read,
            note = excluded.note,
            checked_at = datetime('now')
        """,
        (check.cik, cohort, check.verdict, ",".join(check.concepts),
         check.phrase_hits, check.document, check.documents_read, check.note),
    )


def clear_scale(conn: sqlite3.Connection, fact_id: int) -> None:
    """Remove a stored magnitude for one fact.

    Needed because rejection has to be able to undo. A resolver that declines
    to write leaves whatever an earlier run wrote, so a value the plausibility
    guard was added to catch survives the guard being added.
    """
    conn.execute("DELETE FROM fact_scale WHERE fact_id = ?", (fact_id,))
