-- Basin fact store.
--
-- Append-only. Nothing in this schema updates or deletes a fact row: a
-- restatement or a 10-K/A arrives as a NEW row carrying its own accession, and
-- the row it supersedes stays exactly as it was. Citations point at accessions,
-- and a citation that stops resolving is the failure this design exists to
-- prevent.
--
-- Written for SQLite first; the types and constraints are chosen to port to
-- Postgres without a rewrite.

PRAGMA foreign_keys = ON;


-- The company cohort. Small, hand-curated, versioned in the repo rather than
-- discovered at runtime -- "which 20 companies" is a product decision.
CREATE TABLE IF NOT EXISTS company (
    cik           TEXT PRIMARY KEY,          -- zero-padded 10 digits
    ticker        TEXT,
    name          TEXT NOT NULL,
    basin         TEXT,                      -- Permian, Appalachia, Bakken, ...
    is_operator   INTEGER NOT NULL DEFAULT 1,-- 0 for trusts/midstream/misfiled
    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    notes         TEXT
);


-- One row per filing Basin has seen. Facts reference this, so a citation can
-- always be resolved back to a retrievable document.
CREATE TABLE IF NOT EXISTS filing (
    accession     TEXT PRIMARY KEY,          -- 0000320193-24-000123
    cik           TEXT NOT NULL REFERENCES company(cik),
    form          TEXT NOT NULL,             -- 10-K, 10-Q, 8-K, 10-K/A
    filed_date    TEXT NOT NULL,             -- ISO-8601
    period_end    TEXT,                      -- fiscal period the filing covers
    primary_doc   TEXT,                      -- filename of the main document
    fetched_at    TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS filing_cik_form_idx ON filing (cik, form, filed_date DESC);


-- The fact table. Every value Basin will ever put in a spreadsheet cell.
--
-- extracted_by distinguishes the layers the README evaluates separately:
--   'xbrl'       -- tagged by the filer; exact; no model involved
--   'llm:<name>' -- schema-constrained extraction; source_span is MANDATORY
--   'derived'    -- computed by pure Python from other rows; unit-tested
CREATE TABLE IF NOT EXISTS fact (
    id            INTEGER PRIMARY KEY,
    cik           TEXT NOT NULL REFERENCES company(cik),
    concept_key   TEXT NOT NULL,             -- Basin's stable field name

    value         REAL NOT NULL,
    unit          TEXT NOT NULL,

    period_start  TEXT,                      -- NULL for point-in-time facts
    period_end    TEXT NOT NULL,
    fiscal_year   INTEGER,
    fiscal_period TEXT,                      -- FY, Q1, Q2, Q3, Q4

    accession     TEXT NOT NULL REFERENCES filing(accession),
    form          TEXT NOT NULL,

    -- Provenance of the value itself, as distinct from the document.
    extracted_by  TEXT NOT NULL,
    taxonomy      TEXT,                      -- XBRL rows: which alias hit
    tag           TEXT,
    section       TEXT,                      -- extraction rows: Item 2, MD&A...
    source_span   TEXT,                      -- verbatim quote; verified present

    -- Comparability. A cell that mixes reporting bases without saying so is
    -- the thing that loses an account, so the basis travels with the value.
    basis_note    TEXT,                      -- "includes gathering & transport"
    is_hedged     INTEGER,                   -- realized price: pre/post hedge

    ingested_at   TEXT NOT NULL DEFAULT (datetime('now')),

    -- An LLM-extracted value without a source span is exactly the failure mode
    -- the architecture forbids. Enforce it in the schema, not in review.
    CHECK (extracted_by NOT LIKE 'llm:%' OR source_span IS NOT NULL)
);

-- Re-ingesting the same filing must not duplicate rows, but a genuinely
-- restated value in a NEW accession must still be insertable.
--
-- This is an expression index rather than a table-level UNIQUE because
-- period_start is NULL for point-in-time facts (reserves, standardized
-- measure), and NULL is never equal to NULL -- a plain UNIQUE silently stops
-- deduplicating exactly the rows the panel is built from. COALESCE keeps the
-- comparison total, and is spelled the same way in Postgres.
CREATE UNIQUE INDEX IF NOT EXISTS fact_identity_idx ON fact (
    cik, concept_key, period_end, COALESCE(period_start, ''),
    unit, accession, extracted_by
);

CREATE INDEX IF NOT EXISTS fact_panel_idx
    ON fact (concept_key, period_end DESC, cik);
CREATE INDEX IF NOT EXISTS fact_company_idx
    ON fact (cik, concept_key, period_end DESC);


-- The current view of the panel: for each (company, concept, period), the row
-- from the most recently filed accession. History stays in `fact`; this is
-- only how the table is read.
CREATE VIEW IF NOT EXISTS fact_current AS
SELECT f.*
FROM fact f
JOIN filing fl ON fl.accession = f.accession
WHERE fl.filed_date = (
    SELECT MAX(fl2.filed_date)
    FROM fact f2
    JOIN filing fl2 ON fl2.accession = f2.accession
    WHERE f2.cik = f.cik
      AND f2.concept_key = f.concept_key
      AND f2.period_end = f.period_end
      AND COALESCE(f2.period_start, '') = COALESCE(f.period_start, '')
);


-- Coverage snapshots, so "which concepts does this filer tag" is measured over
-- time rather than re-guessed. Feeds the cohort-selection decision.
CREATE TABLE IF NOT EXISTS coverage_snapshot (
    id            INTEGER PRIMARY KEY,
    cik           TEXT NOT NULL,
    concept_key   TEXT NOT NULL,
    tagged        INTEGER NOT NULL,
    taxonomy      TEXT,
    tag           TEXT,
    observations  INTEGER NOT NULL DEFAULT 0,
    latest_period TEXT,
    measured_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS coverage_cik_idx ON coverage_snapshot (cik, measured_at DESC);
