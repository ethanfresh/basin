"""Read queries over the fact store.

Kept out of the web layer so the panel a browser renders and the panel a test
asserts on are produced by the same code. Every row that carries a value also
carries the accession it came from — a value without its citation should not
be constructible here, let alone reach a screen.
"""

from __future__ import annotations

import sqlite3
from typing import Any

from basin.facts.concepts import ALL_CONCEPTS, BY_KEY

SEC_ARCHIVE = "https://www.sec.gov/Archives/edgar/data"


def filing_url(cik: str, accession: str) -> str:
    """Human-readable SEC index page for a filing.

    The browsable index rather than the raw document: it lists every exhibit,
    which is where a spot-check actually starts.
    """
    return f"{SEC_ARCHIVE}/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params)]


def summary(conn: sqlite3.Connection) -> dict[str, Any]:
    """Headline counts for the dataset as a whole."""
    one = lambda sql: conn.execute(sql).fetchone()[0]  # noqa: E731
    return {
        "companies": one("SELECT COUNT(*) FROM company"),
        "facts": one("SELECT COUNT(*) FROM fact"),
        "cells": one("SELECT COUNT(*) FROM fact_current"),
        "filings": one("SELECT COUNT(*) FROM filing"),
        "concepts": one("SELECT COUNT(DISTINCT concept_key) FROM fact"),
        "earliest_period": one("SELECT MIN(period_end) FROM fact"),
        "latest_period": one("SELECT MAX(period_end) FROM fact"),
        "unit_discontinuities": one("SELECT COUNT(*) FROM unit_discontinuity"),
        "collisions": one("SELECT COUNT(*) FROM fact_collision"),
        "reserve_issues": one(
            "SELECT COUNT(*) FROM reserve_consistency WHERE issue IS NOT NULL"
        ),
    }


def concepts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Concepts present in the store, with their registry labels."""
    counts = {
        r["concept_key"]: r["n"]
        for r in _rows(
            conn,
            "SELECT concept_key, COUNT(*) n FROM fact_current GROUP BY concept_key",
        )
    }
    out = []
    for spec in ALL_CONCEPTS:
        if spec.key in counts:
            out.append(
                {
                    "key": spec.key,
                    "label": spec.label,
                    "cells": counts[spec.key],
                    "notes": spec.notes,
                }
            )
    return out


def periods(conn: sqlite3.Connection, concept_key: str | None = None) -> list[str]:
    """Periods with data, newest first."""
    if concept_key:
        sql = """SELECT DISTINCT period_end FROM fact_current
                 WHERE concept_key = ? ORDER BY period_end DESC"""
        return [r["period_end"] for r in _rows(conn, sql, (concept_key,))]
    sql = "SELECT DISTINCT period_end FROM fact_current ORDER BY period_end DESC"
    return [r["period_end"] for r in _rows(conn, sql)]


def panel(
    conn: sqlite3.Connection,
    concept_key: str,
    period_end: str,
    product: str | None = None,
) -> list[dict[str, Any]]:
    """One concept, one period, every company — the peer comparison table.

    Each row carries its accession, form and originating XBRL tag, plus the
    unit-discontinuity flag for that series, so a cell that needs a caveat
    arrives with the caveat attached rather than looking clean.
    """
    sql = """
        SELECT f.cik, c.name, c.ticker, c.basin,
               f.value, f.unit, f.product,
               f.period_start, f.period_end, f.fiscal_year, f.fiscal_period,
               f.accession, f.form, f.taxonomy, f.tag,
               f.extracted_by, f.source_span, f.section, f.basis_note,
               fl.filed_date,
               (ud.cik IS NOT NULL) AS unit_changed,
               ud.units AS series_units
        FROM fact_current f
        JOIN company c ON c.cik = f.cik
        JOIN filing fl ON fl.accession = f.accession
        LEFT JOIN unit_discontinuity ud
               ON ud.cik = f.cik
              AND ud.concept_key = f.concept_key
              AND ud.product = COALESCE(f.product, '')
        WHERE f.concept_key = ? AND f.period_end = ?
    """
    params: tuple = (concept_key, period_end)
    if product:
        sql += " AND COALESCE(f.product, '') = ?"
        params += (product,)

    # Ordered by unit first, then magnitude *within* the unit.
    #
    # Ranking across units would be a lie. Filers disagree about whether a
    # value already has its unit's prefix applied -- Diamondback reports
    # 2,521,028,000 tagged "MBoe" (base units, presentational label) while
    # Devon reports 2,155 tagged "MMcfe" (scaled to match the label) -- so
    # (value, unit) does not determine magnitude, and a single sorted column
    # would rank by labelling convention rather than by size.
    sql += " ORDER BY f.unit, f.value DESC"

    rows = _rows(conn, sql, params)
    for row in rows:
        row["filing_url"] = filing_url(row["cik"], row["accession"])
        row["unit_changed"] = bool(row["unit_changed"])
    return rows


def unit_groups(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    """Split panel rows into like-for-like groups, one per declared unit.

    A group is the largest set inside which comparison is defensible. Callers
    render groups separately rather than interleaving them, so the table never
    implies a ranking it cannot support.
    """
    groups: dict[str, list[dict[str, Any]]] = {}
    for row in rows:
        groups.setdefault(row["unit"], []).append(row)
    return [
        {"unit": unit, "rows": members, "count": len(members)}
        for unit, members in sorted(groups.items(), key=lambda kv: -len(kv[1]))
    ]


def company_series(conn: sqlite3.Connection, cik: str) -> list[dict[str, Any]]:
    """Every current cell for one company, for the drill-down view."""
    sql = """
        SELECT f.concept_key, f.value, f.unit, f.product,
               f.period_end, f.fiscal_year, f.fiscal_period,
               f.accession, f.form, f.taxonomy, f.tag, f.extracted_by,
               fl.filed_date
        FROM fact_current f
        JOIN filing fl ON fl.accession = f.accession
        WHERE f.cik = ?
        ORDER BY f.concept_key, f.product, f.period_end DESC
    """
    rows = _rows(conn, sql, (cik,))
    for row in rows:
        row["filing_url"] = filing_url(cik, row["accession"])
        row["label"] = BY_KEY[row["concept_key"]].label if row["concept_key"] in BY_KEY else row["concept_key"]
    return rows


def companies(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Cohort members with how much data each actually has."""
    return _rows(
        conn,
        """
        SELECT c.cik, c.name, c.ticker, c.basin,
               COUNT(f.id) AS cells,
               COUNT(DISTINCT f.concept_key) AS concepts,
               MAX(f.period_end) AS latest_period
        FROM company c
        LEFT JOIN fact_current f ON f.cik = c.cik
        GROUP BY c.cik
        ORDER BY concepts DESC, cells DESC, c.name
        """,
    )


def coverage_matrix(conn: sqlite3.Connection) -> dict[str, Any]:
    """Which companies have which concepts, and how recently.

    The point of this view is that absence is as informative as presence: a
    blank cell here is what the extraction layer has to fill.
    """
    concept_keys = [c["key"] for c in concepts(conn)]
    rows = _rows(
        conn,
        """
        SELECT c.cik, c.name, c.ticker, f.concept_key,
               MAX(f.period_end) AS latest_period,
               COUNT(*) AS cells
        FROM company c
        LEFT JOIN fact_current f ON f.cik = c.cik
        GROUP BY c.cik, f.concept_key
        """,
    )

    by_company: dict[str, dict[str, Any]] = {}
    for row in rows:
        entry = by_company.setdefault(
            row["cik"],
            {"cik": row["cik"], "name": row["name"], "ticker": row["ticker"], "concepts": {}},
        )
        if row["concept_key"]:
            entry["concepts"][row["concept_key"]] = {
                "latest_period": row["latest_period"],
                "cells": row["cells"],
            }

    companies_out = sorted(
        by_company.values(), key=lambda c: (-len(c["concepts"]), c["name"] or "")
    )
    return {"concept_keys": concept_keys, "companies": companies_out}


def data_quality(conn: sqlite3.Connection) -> dict[str, Any]:
    """Everything the store knows is questionable about itself.

    Surfaced as a first-class view rather than a log line: a dataset that
    hides its own soft spots is the one that loses an account.
    """
    discontinuities = _rows(
        conn,
        """
        SELECT u.*, c.name, c.ticker
        FROM unit_discontinuity u
        JOIN company c ON c.cik = u.cik
        ORDER BY c.name
        """,
    )
    collisions = _rows(
        conn,
        """
        SELECT k.*, c.name, c.ticker
        FROM fact_collision k
        JOIN company c ON c.cik = k.cik
        ORDER BY c.name, k.period_end DESC
        LIMIT 200
        """,
    )
    fallback_tags = _rows(
        conn,
        """
        SELECT c.name, c.ticker, f.cik, f.concept_key, f.tag,
               COUNT(*) AS cells
        FROM fact_current f
        JOIN company c ON c.cik = f.cik
        WHERE f.tag = 'RevenueFromContractWithCustomerExcludingAssessedTax'
        GROUP BY f.cik, f.concept_key, f.tag
        ORDER BY c.name
        """,
    )
    # Only the rows that fail; the view itself keeps every pairing so the
    # ratio distribution stays available for analysis.
    reserve_issues = _rows(
        conn,
        """
        SELECT r.*, c.name, c.ticker
        FROM reserve_consistency r
        JOIN company c ON c.cik = r.cik
        WHERE r.issue IS NOT NULL
        ORDER BY r.issue, c.name, r.period_end DESC
        """,
    )
    for row in reserve_issues:
        row["developed_url"] = filing_url(row["cik"], row["developed_accession"])
        row["total_url"] = filing_url(row["cik"], row["total_accession"])

    return {
        "unit_discontinuities": discontinuities,
        "collisions": collisions,
        "fallback_tags": fallback_tags,
        "reserve_issues": reserve_issues,
    }


def reserve_ratios(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """Developed-to-total ratios where the pairing is internally coherent.

    A normal producer lands somewhere around 0.45-0.90. Values outside that
    band are worth a look even when the units agree.
    """
    return _rows(
        conn,
        """
        SELECT r.*, c.name, c.ticker
        FROM reserve_consistency r
        JOIN company c ON c.cik = r.cik
        WHERE r.issue IS NULL AND r.ratio IS NOT NULL
        ORDER BY r.ratio
        """,
    )
