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

    -- The product axis (oil / gas / NGL). XBRL dimensions it; the companyfacts
    -- API flattens the axis away, so it is recovered from the unit where the
    -- unit is unambiguous and left NULL where it is not.
    --
    -- This is part of the cell's identity, not decoration. Without it, a
    -- filer's oil realized price and gas realized price collide on one cell
    -- and whichever wins is arbitrary.
    product       TEXT,

    -- Position of `unit` in the concept's preference order. A filer may tag
    -- the same quantity in two units (proved reserves as MMBoe AND MMcfe);
    -- both rows are legitimate, and this is what lets the panel pick one
    -- reproducibly instead of by insertion order.
    unit_rank     INTEGER NOT NULL DEFAULT 0,

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
    cik, concept_key, COALESCE(product, ''), period_end,
    COALESCE(period_start, ''), unit, accession, extracted_by
);

CREATE INDEX IF NOT EXISTS fact_panel_idx
    ON fact (concept_key, period_end DESC, cik);
CREATE INDEX IF NOT EXISTS fact_company_idx
    ON fact (cik, concept_key, period_end DESC);


-- The current view of the panel: exactly one row per cell, where a cell is
-- (company, concept, product, period). History stays in `fact`; this is only
-- how the table is read.
--
-- The ordering inside the window is the whole point, and every term earns its
-- place:
--   filed_date DESC  -- a restatement supersedes the value it restates
--   unit_rank ASC    -- same quantity tagged in two units: prefer the canonical
--   id DESC          -- total order, so the result never depends on scan order
--
-- Product is in the PARTITION, not the ORDER: oil and gas prices are different
-- cells, so both survive rather than one evicting the other.
CREATE VIEW IF NOT EXISTS fact_current AS
SELECT cik, concept_key, value, unit, product, unit_rank,
       period_start, period_end, fiscal_year, fiscal_period,
       accession, form, extracted_by, taxonomy, tag, section, source_span,
       basis_note, is_hedged, ingested_at, id
FROM (
    SELECT f.*,
           ROW_NUMBER() OVER (
               PARTITION BY f.cik, f.concept_key, COALESCE(f.product, ''),
                            f.period_end, COALESCE(f.period_start, '')
               ORDER BY fl.filed_date DESC, f.unit_rank ASC, f.id DESC
           ) AS rn
    FROM fact f
    JOIN filing fl ON fl.accession = f.accession
)
WHERE rn = 1;


-- Cells where the winning row beat a *materially different* value from an
-- equally recent filing. These are the cases where picking a winner is a
-- judgement rather than a restatement, so they are surfaced instead of
-- silently resolved -- a quietly mixed cell is the failure mode that loses an
-- account.
CREATE VIEW IF NOT EXISTS fact_collision AS
SELECT f.cik, f.concept_key, COALESCE(f.product, '') AS product,
       f.period_end,
       COUNT(DISTINCT f.value) AS distinct_values,
       COUNT(DISTINCT f.unit)  AS distinct_units,
       MIN(f.value) AS min_value,
       MAX(f.value) AS max_value,
       GROUP_CONCAT(DISTINCT f.unit) AS units
FROM fact f
JOIN filing fl ON fl.accession = f.accession
GROUP BY f.cik, f.concept_key, COALESCE(f.product, ''), f.period_end,
         COALESCE(f.period_start, ''), fl.filed_date
HAVING COUNT(DISTINCT f.value) > 1;


-- Series where a filer changed the declared unit partway through.
--
-- The panel's unit_rank tie-break assumes competing units are interchangeable
-- labels for one quantity. Sometimes they are not. Devon tags total proved
-- reserves as MMBoe through FY2022 and MMcfe from FY2023, while the values
-- stay continuous (2182 -> 1817 -> 2155); a real BOE-to-cfe change would move
-- the figure about sixfold, so the later unit label is simply wrong.
--
-- Basin does not rewrite a filer's unit -- inventing a corrected label is the
-- one thing worse than reporting the filer's own. It reports the
-- discontinuity so the cell can carry the caveat, which is the same rule the
-- README applies to definition mismatch.
CREATE VIEW IF NOT EXISTS unit_discontinuity AS
SELECT cik, concept_key, COALESCE(product, '') AS product,
       COUNT(DISTINCT unit) AS distinct_units,
       GROUP_CONCAT(DISTINCT unit) AS units,
       MIN(period_end) AS first_period,
       MAX(period_end) AS last_period
FROM fact
GROUP BY cik, concept_key, COALESCE(product, '')
HAVING COUNT(DISTINCT unit) > 1;


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
