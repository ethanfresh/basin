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
    is_operator   INTEGER NOT NULL DEFAULT 1,-- 0 for trusts/royalty vehicles

    -- The cohort. Comparison is legal within one and forbidden across, because
    -- a cohort IS a KPI schema: reserves and lifting cost per BOE mean nothing
    -- for a pipeline, and throughput and distribution coverage mean nothing for
    -- a driller. Putting them in one table would assert a comparability that
    -- does not exist, which is the specific failure this product cannot afford.
    --
    -- Sourced from Finviz's industry classification rather than from SIC, which
    -- is too noisy to assign on: SIC 1311 sweeps in midstream, refiners,
    -- royalty trusts and -- observed in the population -- a biotechnology
    -- company. Recorded with its source and date because the assignment can
    -- change: companies divest midstream assets and E&Ps convert to minerals
    -- vehicles, and a cohort move has to be visible rather than silent.
    cohort        TEXT,                      -- 'Oil & Gas E&P', 'Uranium', ...
    cohort_source TEXT,                      -- 'finviz'
    cohort_as_of  TEXT,                      -- ISO-8601 date of the pull

    -- Domicile, not listing venue. A US-listed filer domiciled abroad files
    -- 20-F or 40-F under IFRS rather than 10-K under US GAAP, so this predicts
    -- whether the Facts layer can reach it at all.
    country       TEXT,
    market_cap_musd REAL,                    -- millions USD, as Finviz exports

    -- Two independent axes, both measured rather than inferred from domicile.
    --
    -- reporting_taxonomy answers "can the Facts layer read this filer": which
    -- XBRL namespace its financials use. Measured from the companyfacts payload,
    -- because 4 of the 23 foreign-domiciled cohort members report us-gaap.
    -- Reserve concepts are a US requirement living in the SEC's srt namespace
    -- and have no IFRS counterpart, so a blank reserve column means "never
    -- tagged by anyone" for an IFRS filer and "this filer did not tag it" for a
    -- us-gaap one -- different findings that look identical without this.
    --
    -- disclosure_regime answers "do two numbers mean the same thing": which
    -- reserve definitions the filer reports under. It follows the annual FORM,
    -- not the accounting standard. 10-K and 20-F are both Subpart 1200; 40-F is
    -- Canadian NI 51-101, where reserves use forecast prices, the headline is
    -- 2P rather than proved, and values are pre-tax. Shell is IFRS and
    -- comparable; Cenovus is IFRS and not. One field could not say that.
    reporting_taxonomy TEXT,             -- 'us-gaap' | 'ifrs-full' | 'unknown'
    taxonomy_note      TEXT,             -- namespace tag counts behind the call
    disclosure_regime  TEXT,             -- 'subpart-1200' | 'ni-51-101'
    regime_note        TEXT,             -- the form, and what it implies

    added_at      TEXT NOT NULL DEFAULT (datetime('now')),
    notes         TEXT
);

CREATE INDEX IF NOT EXISTS company_cohort_idx ON company (cohort);

-- Basin presents companies by ticker and keys them by CIK.
--
-- The two identifiers fail in opposite directions. A CIK is assigned once and
-- never reused. A ticker is released when a company delists and can later be
-- reassigned to an unrelated filer -- so keying facts on one would let two
-- companies' histories merge silently, which is the failure this store exists
-- to prevent. Ticker is therefore the identity in URLs, panels and exports,
-- and never the thing a fact points at.
--
-- NULL is meaningful: it means the filer has no current listing, not that the
-- ticker is unknown. 14 of the first 94 companies are in this state -- taken
-- private, acquired, or delisted -- and they still file, still carry facts, and
-- still have to be citable. The index is partial so they do not collide.
--
-- '' and NULL both meant "no ticker" before that distinction was load-bearing.
-- Only NULL survives a partial unique index, so the blanks are normalised here
-- rather than in a one-off migration: schema_sql() runs on every connect, and a
-- store that predates the index has to be able to open.
UPDATE company SET ticker = NULL WHERE ticker = '';

CREATE UNIQUE INDEX IF NOT EXISTS company_ticker_idx
    ON company (ticker) WHERE ticker IS NOT NULL;


-- One registrant superseding another.
--
-- A redomiciliation or holding-company reorganisation gives the same business a
-- new CIK. The SEC's ticker map follows the ticker to the new entity at once;
-- the filing history stays behind. Reading only the new CIK returns nothing,
-- which is indistinguishable from a filer that tags no data -- so the link has
-- to be recorded, not inferred at read time.
--
-- Evidence, not name-matching: Rule 12g-3(a) makes the successor file Form
-- 8-K12B, that filing names the predecessor, and the name is confirmed against
-- EDGAR's 10-K filers. The accession is kept so the claim is citable like any
-- other. status is 'resolved' or 'unconfirmed'; an unconfirmed row is a lead,
-- not a fact, and nothing reads through it.
CREATE TABLE IF NOT EXISTS registrant_succession (
    successor_cik    TEXT PRIMARY KEY,
    successor_name   TEXT,
    predecessor_cik  TEXT,
    predecessor_name TEXT,
    accession        TEXT,               -- the 8-K12B that establishes it
    filed_date       TEXT,
    status           TEXT NOT NULL,      -- resolved | unconfirmed
    note             TEXT,
    resolved_at      TEXT NOT NULL DEFAULT (datetime('now'))
);

CREATE INDEX IF NOT EXISTS succession_predecessor_idx
    ON registrant_succession (predecessor_cik);


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


-- Whether a cohort member actually produces hydrocarbons.
--
-- Cohort comes from Finviz, which misclassifies: TGS is an Argentine gas
-- pipeline sitting in Oil & Gas Integrated. A non-producer in a producing
-- cohort renders as a blank row in a reserves panel, which reads as a filer
-- that failed to tag something rather than one with nothing to report.
--
-- A verdict, not a fact about a filing, so it is replaced on re-run rather than
-- appended. 'unknown' is distinct from 'non-producer' on purpose: the first
-- means nothing was available to test, the second means the filing was read and
-- holds no reserves.
CREATE TABLE IF NOT EXISTS producer_check (
    cik           TEXT PRIMARY KEY,
    cohort        TEXT,
    verdict       TEXT NOT NULL,      -- producer | non-producer | unknown
    concepts      TEXT,               -- reserve concepts tagged, comma-separated
    phrase_hits   INTEGER NOT NULL DEFAULT 0,
    document      TEXT,               -- what was read, so it can be re-checked
    documents_read INTEGER NOT NULL DEFAULT 0,
    note          TEXT,
    checked_at    TEXT NOT NULL DEFAULT (datetime('now'))
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
    -- Where to look. A citation that says "somewhere in this 3MB document"
    -- is barely better than none, so the coordinates a reader actually uses
    -- are stored: the printed page, the line within the document, and the
    -- "Item N." heading the figure sits under.
    page         INTEGER,            -- printed page, from <hr> page breaks
    line_no      INTEGER,            -- line within the flattened document
    char_offset  INTEGER,
    section      TEXT,               -- the "Item N." heading it sits under
    line_text    TEXT,               -- the whole line, for display
    units_nearby TEXT,               -- units read from the table header / prose
    -- How the figure was located. 'markup' reads the filing's own inline
    -- XBRL, which identifies the fact rather than matching a string that
    -- looks like it; 'text' is the string-search fallback for anything the
    -- filer did not tag.
    method       TEXT,
    anchor       TEXT,               -- e.g. #f-1841, addresses the figure itself
    folio        INTEGER,            -- the page number printed on the page
    scale_declared INTEGER,          -- from the markup, not inferred
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
       v.document    AS verify_document,
       v.line_no     AS verify_line,
       v.section     AS verify_section
FROM fact_current f
LEFT JOIN fact_verification v ON v.fact_id = f.id;


-- The resolved magnitude of a fact, in canonical units.
--
-- Two steps stand behind every row: the scale the document was found to print
-- the figure at (measured, in fact_verification), and which of the resulting
-- candidate readings is the real one (inferred here, by testing the implied
-- value per barrel against the standardized measure).
--
-- The second step is inference, so the evidence travels with it: the ratio it
-- turned on, and the readings it rejected. `value` in `fact` is never
-- touched -- the filer's number stays exactly as filed, and this sits beside
-- it.
CREATE TABLE IF NOT EXISTS fact_scale (
    fact_id         INTEGER PRIMARY KEY REFERENCES fact(id),
    divisor         REAL NOT NULL,     -- stored value / this = as the filing prints it
    canonical_value REAL NOT NULL,
    canonical_unit  TEXT NOT NULL,     -- BOE or USD
    conversion_note TEXT,              -- set when a convention was applied (6:1 gas)
    basis           TEXT NOT NULL,     -- how the divisor was decided
    usd_per_boe     REAL,
    rejected        TEXT,
    note            TEXT,
    resolved_at     TEXT NOT NULL DEFAULT (datetime('now'))
);


-- The panel, in units that can actually be compared across filers.
--
-- Only facts whose magnitude has been resolved appear with a canonical value.
-- Everything else keeps a NULL there rather than a guess, because a cell that
-- is wrong by a factor of a thousand while looking authoritative is the
-- failure this whole apparatus exists to prevent.
CREATE VIEW IF NOT EXISTS fact_canonical AS
SELECT f.*,
       s.canonical_value,
       s.canonical_unit,
       s.divisor        AS scale_divisor,
       s.conversion_note,
       s.basis          AS scale_basis,
       s.usd_per_boe,
       v.status         AS verify_status,
       v.printed        AS verify_printed,
       v.source_span    AS verify_span
FROM fact_current f
LEFT JOIN fact_scale s ON s.fact_id = f.id
LEFT JOIN fact_verification v ON v.fact_id = f.id;


-- The document corpus, parsed.
--
-- Raw HTML lives on disk under data/corpus and stays the archive: it is what
-- was filed, it never changes, and every parse is re-derivable from it. What
-- lives here is the *readable* form -- one row per line, carrying the page,
-- the line number and the "Item N." heading it sits under.
--
-- That shape is chosen for how this gets consumed. An extraction pass wants a
-- section of a filing, not a 3MB file; a citation wants a page and a line; a
-- reviewer wants to search a phrase across every filing a company has made.
-- Storing flattened text as one blob would serve none of those.
CREATE TABLE IF NOT EXISTS document (
    id           INTEGER PRIMARY KEY,
    accession    TEXT NOT NULL,
    name         TEXT NOT NULL,       -- filename as filed
    cik          TEXT,
    form         TEXT,
    filed_date   TEXT,
    kind         TEXT,                -- primary | exhibit
    pages        INTEGER,
    line_count   INTEGER,
    char_count   INTEGER,
    indexed_at   TEXT NOT NULL DEFAULT (datetime('now')),
    UNIQUE (accession, name)
);

CREATE INDEX IF NOT EXISTS document_cik_form_idx ON document (cik, form, filed_date DESC);


CREATE TABLE IF NOT EXISTS document_line (
    document_id  INTEGER NOT NULL REFERENCES document(id),
    line_no      INTEGER NOT NULL,
    page         INTEGER NOT NULL,
    section      TEXT,
    char_offset  INTEGER NOT NULL,
    text         TEXT NOT NULL,
    PRIMARY KEY (document_id, line_no)
);

CREATE INDEX IF NOT EXISTS document_line_page_idx ON document_line (document_id, page);
CREATE INDEX IF NOT EXISTS document_line_section_idx ON document_line (document_id, section);


-- Full-text search over every line of every filing. FTS5 is contentless
-- (`content=`), so the text is stored once in document_line and the index
-- refers to it rather than duplicating a gigabyte.
CREATE VIRTUAL TABLE IF NOT EXISTS document_search USING fts5(
    text,
    content='document_line',
    content_rowid='rowid',
    tokenize='porter unicode61'
);


-- Vision cross-check: does a vision model reading the rendered page agree
-- with what the text parser extracted?
--
-- The parser reads markup; a person reads the rendered page. The two have
-- disagreed before in ways only looking caught -- the ix:header block
-- occupying "sheet 1", header rows of bare years classified as data. This
-- table records systematic checks of parser output against the pixels, one
-- row per checked fact, so parser regressions show up as agreement drops.
CREATE TABLE IF NOT EXISTS vision_check (
    fact_id        INTEGER PRIMARY KEY REFERENCES fact(id),
    sample_group   TEXT NOT NULL,      -- no_header | unit_corrected | control
    value_present  INTEGER,            -- vision found the printed value on the page
    agree_header   INTEGER,            -- vision header matches parser header
    agree_folio    INTEGER,            -- vision page number matches stored folio
    vision_header  TEXT,
    vision_row     TEXT,
    vision_unit    TEXT,
    vision_folio   INTEGER,
    parser_header  TEXT,
    parser_folio   INTEGER,
    note           TEXT,
    model          TEXT,
    checked_at     TEXT NOT NULL DEFAULT (datetime('now'))
);
