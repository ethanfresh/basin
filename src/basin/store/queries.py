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


# Cohort membership is the store's definition of "in scope", and every listing
# and aggregate below requires it.
#
# It used to be an optional narrowing: pass a cohort and see that cohort, pass
# nothing and see everything. But "everything" is not a wider view of the same
# population -- it is a different population. The store keeps every company it
# has ever ingested, because a fact is append-only and a company leaving the
# cohort is not a reason to destroy history that citations depend on. So the
# unfiltered panel drew SIC residue, non-producers dropped on evidence, filers
# since acquired, and candidates the producer gate never admitted: 38 companies
# and 86 rows, most of them nearly empty, indistinguishable from cohort members
# with genuine coverage gaps.
#
# That is the one confusion the panel cannot afford. A blank cell is supposed to
# mean "this filer did not disclose it" -- work for the extraction layer. A
# blank row for a company that was never in scope is not work; it is noise that
# makes the panel understate itself.
#
# Single-fact lookups (fact_locator, citation) and single-company views
# deliberately do NOT filter: a citation has to resolve even when the company
# it names has since left the cohort, or the store's own history becomes
# unreadable.
IN_COHORT = "c.cohort IS NOT NULL"


def filing_url(cik: str, accession: str) -> str:
    """Human-readable SEC index page for a filing.

    The browsable index rather than the raw document: it lists every exhibit,
    which is where a spot-check actually starts.
    """
    return f"{SEC_ARCHIVE}/{int(cik)}/{accession.replace('-', '')}/{accession}-index.htm"


def _rows(conn: sqlite3.Connection, sql: str, params: tuple = ()) -> list[dict[str, Any]]:
    return [dict(r) for r in conn.execute(sql, params)]


def summary(conn: sqlite3.Connection, cohort: str | None = None) -> dict[str, Any]:
    """Headline counts, for one cohort or for the whole store.

    Every count is scoped together or none of them are. A count that quietly
    spans a different population than the view it sits above is worse than no
    count: reserve conflicts read 8,062 over a 75-company grid, and most of that
    number belonged to companies that were never in scope -- SIC-1311 residue
    and filers since acquired. Read as a worklist it was mostly work that does
    not exist.
    """
    def one(sql: str, *, joins: str = "", where: str = "") -> Any:
        """Run a count, adding the company join only when a cohort is selected."""
        if cohort:
            clause = f"{where} AND c.cohort = ?" if where else "WHERE c.cohort = ?"
            return conn.execute(f"{sql} {joins} {clause}", (cohort,)).fetchone()[0]
        return conn.execute(f"{sql} {where}").fetchone()[0]

    # Facts reach company directly; verification and scale reach it through fact.
    via_company = "JOIN company c ON c.cik = f.cik"
    via_fact = "JOIN fact f ON f.id = v.fact_id JOIN company c ON c.cik = f.cik"

    return {
        "cohort": cohort,
        "companies": one("SELECT COUNT(*) FROM company c"),
        "facts": one("SELECT COUNT(*) FROM fact f", joins=via_company),
        "cells": one("SELECT COUNT(*) FROM fact_current f", joins=via_company),
        "filings": one("SELECT COUNT(*) FROM filing f", joins=via_company),
        "concepts": one("SELECT COUNT(DISTINCT f.concept_key) FROM fact f", joins=via_company),
        "earliest_period": one("SELECT MIN(f.period_end) FROM fact f", joins=via_company),
        "latest_period": one("SELECT MAX(f.period_end) FROM fact f", joins=via_company),
        "unit_discontinuities": one(
            "SELECT COUNT(*) FROM unit_discontinuity f",
            joins="JOIN company c ON c.cik = f.cik",
        ),
        "verified": one(
            "SELECT COUNT(*) FROM fact_verification v", joins=via_fact,
            where="WHERE v.status = 'found'",
        ),
        "verify_checked": one("SELECT COUNT(*) FROM fact_verification v", joins=via_fact),
        "comparable": one(
            "SELECT COUNT(*) FROM fact_scale v", joins=via_fact,
        ),
        "collisions": one(
            "SELECT COUNT(*) FROM fact_collision f",
            joins="JOIN company c ON c.cik = f.cik",
        ),
        "reserve_issues": one(
            "SELECT COUNT(*) FROM reserve_consistency f",
            joins="JOIN company c ON c.cik = f.cik",
            where="WHERE f.issue IS NOT NULL",
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


def cohorts(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    """The cohorts present in the store, with how many companies each holds.

    Ordered by size, so the panel's default filter lands on the cohort the
    store actually knows the most about rather than on an alphabetical accident.
    """
    sql = """
        SELECT c.cohort,
               COUNT(*) AS companies,
               SUM(c.is_operator) AS operators,
               SUM(EXISTS(SELECT 1 FROM fact f WHERE f.cik = c.cik)) AS with_facts
        FROM company c
        WHERE c.cohort IS NOT NULL
        GROUP BY c.cohort
        ORDER BY companies DESC
    """
    return _rows(conn, sql)


def panel(
    conn: sqlite3.Connection,
    concept_key: str,
    period_end: str,
    product: str | None = None,
    cohort: str | None = None,
) -> list[dict[str, Any]]:
    """One concept, one period, every company — the peer comparison table.

    Each row carries its accession, form and originating XBRL tag, plus the
    unit-discontinuity flag for that series, so a cell that needs a caveat
    arrives with the caveat attached rather than looking clean.

    *cohort* is not a convenience filter. Comparison is meaningful within a
    cohort and meaningless across one: reserves and lifting cost per BOE do not
    describe a pipeline, and a table mixing an E&P with a midstream partnership
    asserts a comparability that does not exist. Leaving it None returns every
    cohort, which is correct for auditing the store and wrong for reading a
    peer table -- so the panel UI always sends one.
    """
    sql = """
        SELECT f.id, f.cik, c.name, c.ticker, c.basin, c.cohort,
               c.reporting_taxonomy, c.disclosure_regime, c.regime_note,
               f.value, f.unit, f.product,
               f.period_start, f.period_end, f.fiscal_year, f.fiscal_period,
               f.accession, f.form, f.taxonomy, f.tag,
               f.extracted_by, f.source_span, f.section, f.basis_note,
               fl.filed_date,
               (ud.cik IS NOT NULL) AS unit_changed,
               ud.units AS series_units,
               v.status      AS verify_status,
               v.printed     AS verify_printed,
               v.scale_found AS verify_scale,
               v.hits        AS verify_hits,
               v.source_span AS verify_span,
               v.document    AS verify_document,
               sc.canonical_value, sc.canonical_unit, sc.divisor AS scale_divisor,
               sc.conversion_note, sc.usd_per_boe, sc.basis AS scale_basis
        FROM fact_current f
        JOIN company c ON c.cik = f.cik
        JOIN filing fl ON fl.accession = f.accession
        LEFT JOIN unit_discontinuity ud
               ON ud.cik = f.cik
              AND ud.concept_key = f.concept_key
              AND ud.product = COALESCE(f.product, '')
        LEFT JOIN fact_verification v ON v.fact_id = f.id
        LEFT JOIN fact_scale sc ON sc.fact_id = f.id
        WHERE f.concept_key = ? AND f.period_end = ?
          AND """ + IN_COHORT + """
    """
    params: tuple = (concept_key, period_end)
    if product:
        sql += " AND COALESCE(f.product, '') = ?"
        params += (product,)
    if cohort:
        sql += " AND c.cohort = ?"
        params += (cohort,)

    # Ordered by unit, then by company, then by magnitude within the company.
    #
    # Ranking across units would be a lie. Filers disagree about whether a
    # value already has its unit's prefix applied -- Diamondback reports
    # 2,521,028,000 tagged "MBoe" (base units, presentational label) while
    # Devon reports 2,155 tagged "MMcfe" (scaled to match the label) -- so
    # (value, unit) does not determine magnitude, and a single sorted column
    # would rank by labelling convention rather than by size.
    #
    # Within a unit, a company can occupy several rows: XBRL dimensions realized
    # price and reserves by product, so oil, gas and NGL arrive separately. A
    # plain value sort interleaves them -- one filer's oil, another's oil, then
    # back to the first one's gas -- and the reader loses track of whose row
    # they are on. Companies are therefore ranked by their largest product and
    # kept contiguous, which is the ordering a peer table is read in.
    sql += """
        ORDER BY f.unit,
                 MAX(f.value) OVER (PARTITION BY f.unit, f.cik) DESC,
                 f.cik,
                 f.value DESC
    """

    rows = _rows(conn, sql, params)
    for row in rows:
        row["filing_url"] = filing_url(row["cik"], row["accession"])
        row["unit_changed"] = bool(row["unit_changed"])
    return rows


LATEST_PERIOD = "latest"
"""Sentinel period meaning "each company's own most recent"."""


def panel_latest(
    conn: sqlite3.Connection,
    concept_key: str,
    product: str | None = None,
    cohort: str | None = None,
) -> list[dict[str, Any]]:
    """The panel, taking each company's most recently reported period.

    Pinning one period_end is the more defensible thing to do and it costs
    coverage twice over. Fiscal years do not line up -- 7 cohort members close
    in March, June, September or October rather than December -- so a December
    filter drops them entirely rather than showing them a quarter out. And
    filers stop tagging: for proved developed reserves, the best single period
    reaches 31 companies while their own latest reaches 38.

    The cost is that the column mixes periods, and that cost is real: some
    filers' newest tagged reserve figure is from 2011. So every row carries the
    period it came from and how stale that is, and the caller is expected to
    show both -- an undated latest-reported column would be the most quietly
    misleading thing in the product.
    """
    sql = """
        WITH ranked AS (
            SELECT f.*, ROW_NUMBER() OVER (
                       PARTITION BY f.cik, f.concept_key, COALESCE(f.product, '')
                       ORDER BY f.period_end DESC
                   ) AS recency
            FROM fact_current f
            WHERE f.concept_key = ?
        )
        SELECT ranked.id, ranked.cik, c.name, c.ticker, c.basin, c.cohort,
               c.reporting_taxonomy, c.disclosure_regime, c.regime_note,
               ranked.value, ranked.unit, ranked.product,
               ranked.period_start, ranked.period_end,
               ranked.fiscal_year, ranked.fiscal_period,
               ranked.accession, ranked.form, ranked.taxonomy, ranked.tag,
               ranked.extracted_by, ranked.source_span, ranked.section,
               ranked.basis_note,
               fl.filed_date,
               (ud.cik IS NOT NULL) AS unit_changed,
               ud.units AS series_units,
               v.status AS verify_status, v.printed AS verify_printed,
               v.scale_found AS verify_scale, v.hits AS verify_hits,
               v.source_span AS verify_span, v.document AS verify_document,
               sc.canonical_value, sc.canonical_unit, sc.divisor AS scale_divisor,
               sc.conversion_note, sc.usd_per_boe, sc.basis AS scale_basis
        FROM ranked
        JOIN company c ON c.cik = ranked.cik
        JOIN filing fl ON fl.accession = ranked.accession
        LEFT JOIN unit_discontinuity ud
               ON ud.cik = ranked.cik AND ud.concept_key = ranked.concept_key
              AND ud.product = COALESCE(ranked.product, '')
        LEFT JOIN fact_verification v ON v.fact_id = ranked.id
        LEFT JOIN fact_scale sc ON sc.fact_id = ranked.id
        WHERE ranked.recency = 1 AND """ + IN_COHORT + """
    """
    params: tuple = (concept_key,)
    if product:
        sql += " AND COALESCE(ranked.product, '') = ?"
        params += (product,)
    if cohort:
        sql += " AND c.cohort = ?"
        params += (cohort,)
    sql += """
        ORDER BY ranked.unit,
                 MAX(ranked.value) OVER (PARTITION BY ranked.unit, ranked.cik) DESC,
                 ranked.cik,
                 ranked.value DESC
    """

    rows = _rows(conn, sql, params)
    newest = max((r["period_end"] for r in rows if r["period_end"]), default=None)
    for row in rows:
        row["filing_url"] = filing_url(row["cik"], row["accession"])
        row["unit_changed"] = bool(row["unit_changed"])
        row["periods_behind"] = _years_behind(row["period_end"], newest)
    return rows


def _years_behind(period_end: str | None, newest: str | None) -> int | None:
    """Whole years between this row's period and the newest in the panel.

    Reported rather than used to filter. A stale figure is still the filer's
    most recent disclosure, and hiding it would misrepresent the panel as
    complete; the reader decides whether five-year-old reserves are usable.
    """
    if not period_end or not newest:
        return None
    return int(newest[:4]) - int(period_end[:4])


def panel_wide(
    conn: sqlite3.Connection,
    period_end: str | None = None,
    product: str | None = None,
    cohort: str | None = None,
    include_uncohorted: bool = False,
) -> dict[str, Any]:
    """The consolidated panel: one row per company and product, one column per KPI.

    This is the shape the product is named for -- everything a filer reports,
    side by side, rather than one metric at a time.

    Rows are keyed by ``(company, product)`` rather than by company alone. XBRL
    dimensions reserves and realized price by oil, gas and NGL and the
    companyfacts API flattens the dimension away, so a filer legitimately holds
    several values for one concept. Collapsing them into one cell would either
    drop data or invent a total the filer never reported; a row per product
    keeps every value visible and every cell single-valued.

    ``period_end`` of None means each company's own latest for each concept,
    which is what makes the table dense: fiscal years do not align and filers
    stop tagging concepts at different times, so any single period leaves large
    holes that are an artifact of the filter rather than of the disclosure.

    No cross-company ranking is applied. A row set spans nine concepts in
    however many units their filers chose, and there is no single column the
    whole table can be ordered by without asserting a comparison that does not
    hold. Sorting is the caller's decision, per column, and the unit count on
    each column is returned so the caller can refuse when it would be a lie.

    **Only cohort members appear.** The store is append-only and keeps the facts
    of companies that have left the cohort or were never admitted to one --
    refiners, midstream partnerships, a biotechnology company, and candidates
    the producer check has not yet adjudicated. They carry real facts, so they
    were indistinguishable from members in the panel, and they were sparse
    because they are not producers: 38 of the 123 companies shown, 86 rows,
    filling columns that do not apply to them. A row whose emptiness means
    "this company does not belong in this table" reads as a coverage gap, which
    is the specific misrepresentation the panel is supposed to prevent.

    ``include_uncohorted`` restores them, for coverage reporting that needs to
    ask what the store holds rather than what the panel shows.
    """
    concept_keys = [c["key"] for c in concepts(conn)]

    sql = """
        WITH ranked AS (
            SELECT f.*, ROW_NUMBER() OVER (
                       PARTITION BY f.cik, f.concept_key, COALESCE(f.product, '')
                       ORDER BY f.period_end DESC
                   ) AS recency
            FROM fact_current f
            {period_filter}
        )
        SELECT r.id, r.cik, c.name, c.ticker, c.cohort,
               c.reporting_taxonomy, c.disclosure_regime, c.regime_note,
               r.concept_key, r.value, r.unit, r.product, r.period_end,
               r.accession, r.form, r.taxonomy, r.tag,
               v.status AS verify_status,
               sc.canonical_value, sc.canonical_unit
        FROM ranked r
        JOIN company c ON c.cik = r.cik
        LEFT JOIN fact_verification v ON v.fact_id = r.id
        LEFT JOIN fact_scale sc ON sc.fact_id = r.id
        WHERE r.recency = 1
    """
    if not include_uncohorted:
        sql += " AND c.cohort IS NOT NULL"
    params: tuple = ()
    if period_end:
        sql = sql.format(period_filter="WHERE f.period_end = ?")
        params += (period_end,)
    else:
        sql = sql.format(period_filter="")
    if product:
        sql += " AND COALESCE(r.product, '') = ?"
        params += (product,)
    if cohort:
        sql += " AND c.cohort = ?"
        params += (cohort,)

    rows = _rows(conn, sql, params)

    by_row: dict[tuple[str, str], dict[str, Any]] = {}
    units: dict[str, set[str]] = {k: set() for k in concept_keys}
    for row in rows:
        key = (row["cik"], row["product"] or "")
        entry = by_row.setdefault(key, {
            "cik": row["cik"],
            "name": row["name"],
            "ticker": row["ticker"],
            "cohort": row["cohort"],
            "reporting_taxonomy": row["reporting_taxonomy"],
            "disclosure_regime": row["disclosure_regime"],
            "regime_note": row["regime_note"],
            "product": row["product"],
            "cells": {},
        })
        entry["cells"][row["concept_key"]] = {
            "fact_id": row["id"],
            "value": row["value"],
            "unit": row["unit"],
            "period_end": row["period_end"],
            "accession": row["accession"],
            "form": row["form"],
            "tag": f'{row["taxonomy"]}:{row["tag"]}',
            "verified": row["verify_status"] == "found",
            "canonical_value": row["canonical_value"],
            "canonical_unit": row["canonical_unit"],
            "filing_url": filing_url(row["cik"], row["accession"]),
        }
        units[row["concept_key"]].add(row["unit"])

    out = sorted(
        by_row.values(),
        key=lambda r: (-len(r["cells"]), r["ticker"] or "zzz", r["product"] or ""),
    )
    return {
        "concept_keys": concept_keys,
        # How many declared units each column spans. A column with one unit can
        # be ranked; a column with several cannot, and the caller is told which
        # rather than left to find out.
        "column_units": {k: sorted(v) for k, v in units.items()},
        "rows": out,
    }


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
        WHERE """ + IN_COHORT + """
        GROUP BY c.cik
        ORDER BY concepts DESC, cells DESC, c.name
        """,
    )


def coverage_matrix(
    conn: sqlite3.Connection, cohort: str | None = None
) -> dict[str, Any]:
    """Which companies have which concepts, and how recently.

    The point of this view is that absence is as informative as presence: a
    blank cell here is what the extraction layer has to fill.

    Which makes the scope load-bearing. Unfiltered, the grid draws that mandate
    over every company the store has ever ingested -- including the SIC-1311
    residue that was never in a cohort, and filers since acquired or taken
    private. Those blanks are not work to be done; they are companies out of
    scope. Passing a cohort measures the mandate the product actually has.
    """
    concept_keys = [c["key"] for c in concepts(conn)]
    sql = """
        SELECT c.cik, c.name, c.ticker, f.concept_key,
               MAX(f.period_end) AS latest_period,
               COUNT(*) AS cells
        FROM company c
        LEFT JOIN fact_current f ON f.cik = c.cik
        WHERE """ + IN_COHORT + """
    """
    params: tuple = ()
    if cohort:
        sql += " AND c.cohort = ?"
        params = (cohort,)
    sql += " GROUP BY c.cik, f.concept_key"
    rows = _rows(conn, sql, params)

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
    return {
        "concept_keys": concept_keys,
        "companies": companies_out,
        "cohort": cohort,
    }


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

    alias_validation = _rows(
        conn,
        """
        SELECT a.cik, c.name, c.ticker, a.status, a.coherent_periods,
               a.tested_periods, a.median_error, a.note,
               GROUP_CONCAT(a.concept_key || '=' || a.tag, ' | ') AS chosen
        FROM alias_validation a
        JOIN company c ON c.cik = a.cik
        WHERE a.status IN ('drifted', 'incoherent')
        GROUP BY a.cik
        ORDER BY a.status, c.name
        """,
    )

    # Producers the current scope cannot reach.
    #
    # Only private filers -- no listing, still filing periodic reports. A
    # company that filed Form 15 and stopped reporting was acquired, and is not
    # a gap in coverage: there is nothing left to cover. Conflating the two
    # overstated this list at 9 when the real number is 4.
    #
    # It belongs beside the other things the store knows are wrong with it, not
    # buried in a column nobody queries. Scope is traded US securities and stays
    # that way; what is not acceptable is the exclusion being invisible.
    unreached = _rows(
        conn,
        """
        SELECT c.cik, c.name, c.last_filing_date, c.listing_note,
               COUNT(DISTINCT f.concept_key) AS concepts,
               MAX(f.period_end) AS latest_period
        FROM company c
        JOIN fact f ON f.cik = c.cik
        WHERE c.listing_status = 'private-filer'
        GROUP BY c.cik
        -- A reserve or production concept, not merely any concept. Revenue and
        -- capex alone describe a company that spends money and sells something
        -- -- Rivulet Entertainment survived the SIC-1311 sweep on exactly that
        -- basis. Only a filer with a reserve base is a producer being missed.
        HAVING SUM(
            f.concept_key IN (
                'proved_developed_reserves_boe', 'proved_undeveloped_reserves_boe',
                'proved_reserves_boe', 'standardized_measure', 'production_volume'
            )
        ) > 0
        ORDER BY c.last_filing_date DESC
        """,
    )

    return {
        "alias_validation": alias_validation,
        "unit_discontinuities": discontinuities,
        "collisions": collisions,
        "fallback_tags": fallback_tags,
        "reserve_issues": reserve_issues,
        "unreached": unreached,
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
        WHERE r.issue IS NULL AND r.ratio IS NOT NULL AND """ + IN_COHORT + """
        ORDER BY r.ratio
        """,
    )


def fact_locator(conn: sqlite3.Connection, fact_id: int) -> dict[str, Any] | None:
    """Everything needed to find one number in its filing, by hand.

    The point of the store is that any cell can be checked against the
    document behind it, so this returns the document, the Item section, the
    line, the character offset, and the verbatim text around the figure --
    plus the resolved magnitude and the evidence for it.

    On page numbers: EDGAR serves filings as HTML, which has none. The page
    numbers printed in a 10-K belong to its print rendering and are not in the
    markup, so the locator gives the line and the Item heading, which are what
    the document itself actually defines.
    """
    row = conn.execute(
        """
        SELECT f.id, f.cik, c.name, c.ticker, f.concept_key, f.value, f.unit,
               f.product, f.period_start, f.period_end, f.fiscal_period,
               f.accession, f.form, f.taxonomy, f.tag, f.extracted_by,
               fl.filed_date,
               v.status AS verify_status, v.document, v.printed, v.scale_found,
               v.scale_label, v.hits, v.source_span, v.char_offset, v.line_no,
               v.section, v.units_nearby,
               s.canonical_value, s.canonical_unit, s.divisor AS scale_divisor,
               s.conversion_note, s.basis AS scale_basis, s.usd_per_boe,
               s.rejected AS scale_rejected
        FROM fact_current f
        JOIN company c ON c.cik = f.cik
        JOIN filing fl ON fl.accession = f.accession
        LEFT JOIN fact_verification v ON v.fact_id = f.id
        LEFT JOIN fact_scale s ON s.fact_id = f.id
        WHERE f.id = ?
        """,
        (fact_id,),
    ).fetchone()
    if row is None:
        return None

    out = dict(row)
    out["filing_url"] = filing_url(out["cik"], out["accession"])
    out["document_url"] = (
        f"{SEC_ARCHIVE}/{int(out['cik'])}/{out['accession'].replace('-', '')}/{out['document']}"
        if out.get("document")
        else None
    )
    out["label"] = (
        BY_KEY[out["concept_key"]].label
        if out["concept_key"] in BY_KEY
        else out["concept_key"]
    )
    return out


def citation(conn: sqlite3.Connection, fact_id: int) -> dict[str, Any] | None:
    """Everything needed to check one number against its source, in one call.

    This is the product's core promise made operable: not "here is a number
    and an accession", but the filing, the document, the page, the line, the
    heading it sits under, and the line as printed — enough to open the filing
    and land on the figure.
    """
    row = conn.execute(
        """
        SELECT f.id, f.cik, c.name, c.ticker, f.concept_key, f.value, f.unit,
               f.product, f.period_end, f.period_start, f.fiscal_year,
               f.accession, f.form, f.taxonomy, f.tag, f.extracted_by,
               fl.filed_date, fl.primary_doc,
               v.status AS verify_status, v.document, v.printed, v.page,
               v.line_no, v.section, v.line_text, v.source_span, v.hits,
               v.scale_found, v.scale_label, v.checked_at,
               v.method, v.anchor, v.folio, v.scale_declared, v.note,
               sc.canonical_value, sc.canonical_unit, sc.divisor AS scale_divisor,
               sc.conversion_note, sc.usd_per_boe, sc.basis AS scale_basis,
               sc.rejected AS scale_rejected
        FROM fact f
        JOIN company c ON c.cik = f.cik
        JOIN filing fl ON fl.accession = f.accession
        LEFT JOIN fact_verification v ON v.fact_id = f.id
        LEFT JOIN fact_scale sc ON sc.fact_id = f.id
        WHERE f.id = ?
        """,
        (fact_id,),
    ).fetchone()
    if row is None:
        return None

    out = dict(row)
    out["label"] = BY_KEY[out["concept_key"]].label if out["concept_key"] in BY_KEY else out["concept_key"]
    out["filing_url"] = filing_url(out["cik"], out["accession"])
    if out.get("document"):
        out["document_url"] = (
            f"{SEC_ARCHIVE}/{int(out['cik'])}/"
            f"{out['accession'].replace('-', '')}/{out['document']}"
        )
    return out


def company_concepts(
    conn: sqlite3.Connection, cohort: str | None = None
) -> dict[str, list[str]]:
    """Which KPIs each company actually has, keyed by CIK.

    Fetched once and held by the caller rather than joined into the panel:
    the panel shows one concept, and this answers what *else* is behind each
    row. A filer's coverage does not change between period selections, so
    re-querying it on every panel load would be waste.
    """
    sql = """
        SELECT f.cik, f.concept_key
        FROM fact_current f
        JOIN company c ON c.cik = f.cik
        WHERE c.cohort IS NOT NULL
    """
    params: tuple = ()
    if cohort:
        sql += " AND c.cohort = ?"
        params = (cohort,)
    sql += " GROUP BY f.cik, f.concept_key"

    out: dict[str, list[str]] = {}
    for row in _rows(conn, sql, params):
        out.setdefault(row["cik"], []).append(row["concept_key"])
    return out


def company_history(
    conn: sqlite3.Connection,
    cik: str,
    concept_key: str,
    product: str | None = None,
) -> dict[str, Any]:
    """Everything one company has ever reported for one concept.

    The panel shows a single period. This is what stands behind that cell: the
    whole reported history, so a number can be read against the filer's own
    past rather than only against its peers.

    Series are split by ``(product, unit)`` rather than drawn as one line. Both
    halves of that key matter. Product, because XBRL dimensions reserves and
    realized price by oil, gas and NGL, and the companyfacts API flattens the
    dimension away -- one line through all three would be three quantities
    pretending to be a trend. Unit, because filers relabel: Devon tags proved
    reserves ``MMBoe`` through FY2022 and ``MMcfe`` from FY2023 while the values
    run continuously, and joining those points draws a change that never
    happened. A break in the line is the honest rendering of a break in the
    series.

    Unlike :func:`trends` this keeps non-annual periods, because the question is
    what this filer reported and when, not how peers compare.
    """
    sql = """
        SELECT f.id, f.value, f.unit, f.product, f.period_end,
               f.fiscal_year, f.fiscal_period, f.accession, f.form,
               f.taxonomy, f.tag,
               fl.filed_date,
               sc.canonical_value, sc.canonical_unit,
               v.status AS verify_status
        FROM fact_current f
        JOIN filing fl ON fl.accession = f.accession
        LEFT JOIN fact_scale sc ON sc.fact_id = f.id
        LEFT JOIN fact_verification v ON v.fact_id = f.id
        WHERE f.cik = ? AND f.concept_key = ?
    """
    params: tuple = (cik, concept_key)
    if product:
        sql += " AND COALESCE(f.product, '') = ?"
        params += (product,)
    sql += " ORDER BY f.period_end"

    rows = _rows(conn, sql, params)
    company = conn.execute(
        "SELECT ticker, name, cohort, reporting_taxonomy, disclosure_regime "
        "FROM company WHERE cik = ?", (cik,)
    ).fetchone()

    series: dict[tuple[str, str], dict[str, Any]] = {}
    for row in rows:
        key = (row["product"] or "", row["unit"])
        entry = series.setdefault(key, {
            "product": row["product"],
            "unit": row["unit"],
            "points": [],
        })
        entry["points"].append({
            "period": row["period_end"],
            "value": row["value"],
            "canonical_value": row["canonical_value"],
            "canonical_unit": row["canonical_unit"],
            "fact_id": row["id"],
            "accession": row["accession"],
            "form": row["form"],
            "filed_date": row["filed_date"],
            "tag": f'{row["taxonomy"]}:{row["tag"]}',
            "verified": row["verify_status"] == "found",
            "filing_url": filing_url(cik, row["accession"]),
        })

    ordered = sorted(series.values(), key=lambda s: (s["product"] or "", s["unit"]))
    units = {s["unit"] for s in ordered}
    return {
        "cik": cik,
        "ticker": company["ticker"] if company else None,
        "name": company["name"] if company else cik,
        "concept": concept_key,
        "label": BY_KEY[concept_key].label if concept_key in BY_KEY else concept_key,
        "series": ordered,
        "periods": sorted({p["period"] for s in ordered for p in s["points"]}),
        "unit_changed": len(units) > 1,
    }


def trends(
    conn: sqlite3.Connection,
    concept_key: str,
    *,
    normalized: bool = True,
    limit: int = 12,
) -> dict[str, Any]:
    """One concept as a time series per company.

    Only annual periods are used, because mixing a Q3 figure into an annual
    series produces a chart that looks like a collapse and is really a change
    of period length. Series are ranked by their latest value and truncated,
    since forty overlapping lines communicate nothing.
    """
    rows = _rows(
        conn,
        """
        SELECT f.cik, c.name, c.ticker, f.period_end, f.value, f.unit,
               f.accession, f.id,
               sc.canonical_value, sc.canonical_unit
        FROM fact_current f
        JOIN company c ON c.cik = f.cik
        LEFT JOIN fact_scale sc ON sc.fact_id = f.id
        WHERE f.concept_key = ?
          AND f.period_end LIKE '%-12-31'
          AND """ + IN_COHORT + """
        ORDER BY f.cik, f.period_end
        """,
        (concept_key,),
    )

    series: dict[str, dict[str, Any]] = {}
    for row in rows:
        value = row["canonical_value"] if normalized else row["value"]
        if value is None:
            continue
        entry = series.setdefault(
            row["cik"],
            {
                "cik": row["cik"],
                "name": row["name"],
                "ticker": row["ticker"],
                "unit": row["canonical_unit"] if normalized else row["unit"],
                "points": [],
            },
        )
        entry["points"].append(
            {
                "period": row["period_end"],
                "value": value,
                "fact_id": row["id"],
                "as_filed": row["value"],
                "filed_unit": row["unit"],
            }
        )

    # A single point draws no line, and a chart of dots is not a trend.
    usable = [s for s in series.values() if len(s["points"]) >= 2]
    usable.sort(key=lambda s: s["points"][-1]["value"], reverse=True)
    dropped = len(series) - len(usable)
    return {
        "concept": concept_key,
        "normalized": normalized,
        "series": usable[:limit],
        "dropped_single_point": dropped,
        "omitted": max(0, len(usable) - limit),
    }
