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

    -- Stored generated columns, not COALESCE() expressions in the index.
    --
    -- The identity index used to be an expression index. Under SQLite 3.41 a
    -- plan that scans it can return NULL for `value` -- a plain
    -- `COUNT(DISTINCT value) > 1` query returned 0 rows with the index and
    -- 240 with NOT INDEXED. Wrong answers, silently, depending on which plan
    -- the optimiser picked.
    --
    -- Generating the keys as real columns keeps the same semantics (NULL
    -- product and NULL period_start still compare equal) while leaving the
    -- index over plain columns, which does not take that code path.
    -- Postgres 12+ spells generated columns the same way.
    product_key      TEXT GENERATED ALWAYS AS (COALESCE(product, '')) STORED,
    period_start_key TEXT GENERATED ALWAYS AS (COALESCE(period_start, '')) STORED,

    -- An LLM-extracted value without a source span is exactly the failure mode
    -- the architecture forbids. Enforce it in the schema, not in review.
    CHECK (extracted_by NOT LIKE 'llm:%' OR source_span IS NOT NULL)
);

-- Re-ingesting the same filing must not duplicate rows, but a genuinely
-- restated value in a NEW accession must still be insertable.
--
-- It indexes the generated key columns rather than the nullable originals:
-- period_start is NULL for point-in-time facts (reserves, standardized
-- measure), and NULL is never equal to NULL, so a plain UNIQUE over the raw
-- columns silently stops deduplicating exactly the rows the panel is built
-- from.
CREATE UNIQUE INDEX IF NOT EXISTS fact_identity_idx ON fact (
    cik, concept_key, product_key, period_end,
    period_start_key, unit, accession, extracted_by
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
               PARTITION BY f.cik, f.concept_key, f.product_key,
                            f.period_end, f.period_start_key
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
SELECT f.cik, f.concept_key, f.product_key AS product,
       f.period_end,
       COUNT(DISTINCT f.value) AS distinct_values,
       COUNT(DISTINCT f.unit)  AS distinct_units,
       MIN(f.value) AS min_value,
       MAX(f.value) AS max_value,
       GROUP_CONCAT(DISTINCT f.unit) AS units
FROM fact f
JOIN filing fl ON fl.accession = f.accession
GROUP BY f.cik, f.concept_key, f.product_key, f.period_end,
         f.period_start_key, fl.filed_date
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
SELECT cik, concept_key, product_key AS product,
       COUNT(DISTINCT unit) AS distinct_units,
       GROUP_CONCAT(DISTINCT unit) AS units,
       MIN(period_end) AS first_period,
       MAX(period_end) AS last_period
FROM fact
GROUP BY cik, concept_key, product_key
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


-- Reserve arithmetic that does not add up.
--
-- Two identities have to hold inside a single filer's own numbers:
--   developed <= total          (developed reserves are a subset)
--   developed + undeveloped = total
-- and all three must be quoted in one unit.
--
-- The second identity is the useful one, because it is what catches a wrong
-- *alias* choice. Continental tags ProvedDevelopedReservesBOE1 (960 MMBoe) and
-- ProvedUndevelopedReserveBOE1 (1,825 MMBoe), which sum to 2,785 -- while the
-- tag picked for total, ProvedDevelopedAndUndevelopedReserveNetEnergy, reads
-- 865. The components agree with each other and disagree with the total, so
-- the total is the tag that does not mean what the registry assumed.
--
-- Neither check needs outside knowledge of how large a company is, which is
-- what makes them cheap enough to run over the whole cohort.
CREATE VIEW IF NOT EXISTS reserve_consistency AS
SELECT d.cik,
       d.period_end,
       d.value      AS developed_value,
       d.unit       AS developed_unit,
       u.value      AS undeveloped_value,
       u.unit       AS undeveloped_unit,
       t.value      AS total_value,
       t.unit       AS total_unit,
       d.accession  AS developed_accession,
       t.accession  AS total_accession,
       d.tag        AS developed_tag,
       t.tag        AS total_tag,
       CASE WHEN d.unit = t.unit AND t.value <> 0
            THEN d.value / t.value END AS ratio,
       CASE
           WHEN d.unit <> t.unit         THEN 'units differ'
           WHEN t.value = 0              THEN 'total proved is zero'
           -- Ordered before the subset check on purpose. When all three are
           -- present and the two components agree with each other but not
           -- with the total, that localises the fault to the total's tag.
           -- 'developed exceeds total' would also be true, but says only that
           -- something is wrong, not which value to distrust.
           WHEN u.value IS NOT NULL
                AND u.unit = d.unit
                AND ABS(d.value + u.value - t.value) > t.value * 0.03
                                         THEN 'components do not sum to total'
           WHEN d.value > t.value * 1.02 THEN 'developed exceeds total'
           WHEN d.value = t.value        THEN 'developed equals total'
       END AS issue
FROM fact_current d
JOIN fact_current t
  ON  t.cik         = d.cik
  AND t.period_end  = d.period_end
  AND t.concept_key = 'proved_reserves_boe'
LEFT JOIN fact_current u
  ON  u.cik         = d.cik
  AND u.period_end  = d.period_end
  AND u.concept_key = 'proved_undeveloped_reserves_boe'
WHERE d.concept_key = 'proved_developed_reserves_boe';


-- What the per-filer alias validation decided, and on what evidence.
--
-- Alias choice is a measurement here, not a default: developed + undeveloped
-- = total is an identity that a *combination* of tags either satisfies or
-- does not, so the combination whose numbers agree is the one that gets used.
-- The evidence is kept because "we picked this tag" is a claim that has to be
-- auditable like any other value in the store.
CREATE TABLE IF NOT EXISTS alias_validation (
    cik              TEXT NOT NULL,
    family           TEXT NOT NULL,      -- 'reserves'
    concept_key      TEXT NOT NULL,
    taxonomy         TEXT,
    tag              TEXT,
    unit             TEXT,
    status           TEXT NOT NULL,      -- validated | incoherent | insufficient
    coherent_periods INTEGER NOT NULL DEFAULT 0,
    tested_periods   INTEGER NOT NULL DEFAULT 0,
    median_error     REAL,
    note             TEXT,
    checked_at       TEXT NOT NULL DEFAULT (datetime('now')),
    PRIMARY KEY (cik, family, concept_key)
);


-- Document verification: has this value been seen in the filing it cites?
--
-- Every fact so far arrived from the XBRL API, where the accession is
-- asserted by the SEC rather than confirmed against the document. The
-- README's rule is that a citation is not done until the cited text has been
-- found in the cited document, so this table records that check.
--
-- scale_found is the second reason to do it. Diamondback's proved reserves
-- arrive as 2,521,028,000 tagged MBoe while the 10-K prints 2,521,028; the
-- document is the only place the presentation scale is stated, and it is
-- exactly what the peer panel needs before it can rank across filers.
CREATE TABLE IF NOT EXISTS fact_verification (
    fact_id      INTEGER PRIMARY KEY REFERENCES fact(id),
    status       TEXT NOT NULL,      -- found | not_found | unavailable
    document     TEXT,               -- primary document filename
    printed      TEXT,               -- the literal string matched
    scale_found  REAL,               -- stored value / printed value
    scale_label  TEXT,
    hits         INTEGER,            -- occurrences; 1 is strong, 50 is weak
    source_span  TEXT,               -- verbatim quote around the match
    note         TEXT,
    checked_at   TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS fact_verification_status_idx
    ON fact_verification (status);


-- Facts joined to their verification, which is how the panel should read
-- them once verification has run over the cohort.
CREATE VIEW IF NOT EXISTS fact_verified AS
SELECT f.*,
       v.status      AS verify_status,
       v.printed     AS verify_printed,
       v.scale_found AS verify_scale,
       v.hits        AS verify_hits,
       v.source_span AS verify_span,
       v.document    AS verify_document
FROM fact_current f
LEFT JOIN fact_verification v ON v.fact_id = f.id;
