# Panel gaps: why locatable is not yet extracted

**Scope.** The 89 producer-verified cohort members and the nine panel columns.
Every number below was measured against the store and corpus on disk.

**Last measured:** after the standardized-measure and cost-per-unit work. The
previous revision of this document is superseded — three of its six problems are
resolved, and its diagnosis of the cost column was wrong in a way worth reading
about, because the same mistake is easy to repeat.

---

## Where the columns stand

| Panel column | XBRL | Table | Either | Locatable | Shortfall |
|---|---|---|---|---|---|
| Oil & gas revenue | 71 | — | **71** | — | — |
| Capital expenditure | 71 | — | **71** | — | — |
| Standardized measure | 47 | 56 | **66** | 82 | 16 |
| Proved developed reserves | 40 | 44 | **58** | 83 | 25 |
| Proved undeveloped reserves | 37 | 44 | **55** | 82 | 27 |
| Total proved reserves | 43 | 29 | **55** | 69 | 14 |
| Production volume | 37 | 20 | **41** | — | — |
| Average realized price | 8 | 31 | **37** | 58 | 21 |
| Production cost per unit | 2 | 37 | **39** | 66 | 27 |

*Locatable* counts filers for whom the full-text index finds that disclosure's
own row language in an annual report on disk. *Either* is the panel's actual
coverage from both paths.

**Production volume has no locatable figure and that is a measurement gap, not a
finding.** The production locator searches for price and cost phrases; it does
not search for volume language, so the volume category is only ever seen when a
volume row happens to sit among lines a price or cost term already returned.
The same applies to the cash-flow spec's discount category. Either add the terms
or stop reporting those two numbers — do not read the current ones.

Since the previous revision: standardized measure 47 → 66, production cost 2 →
39, realized price 8 → 37, proved developed 50 → 58.

## How much of the table output is trustworthy

Every table-read row that has an XBRL counterpart at the same concept, product,
period and unit, compared:

| Source | Rows stored | Comparisons | Agree | Digits agree, magnitude differs | Disagree | On value |
|---|---|---|---|---|---|---|
| `table:reserves` | 9,985 | 4,548 | 3,162 | 1,129 | 257 | **94.3%** |
| `table:cashflow` | 674 | 1,228 | 1,138 | 40 | 50 | **95.9%** |
| `table:production` | 3,724 | 423 | 283 | 68 | 72 | **83.0%** |

Comparisons exceed rows for the cash-flow source because one table reading is
compared against every XBRL row at that key, and filers restate the measure
across successive filings under different accessions. They fall short of rows
for the other two because most table readings have no XBRL counterpart at all —
which is the point of extracting them.

The middle column is not a defect in the extractor. A table figure is the figure
as printed and its unit is its column header, so it cannot carry a magnitude
error; where the digits match and the magnitude does not, it is the filer's own
XBRL that declares the wrong unit — Talos tags 85,007 MMBbls against its own
column reading (MBbls). That is 24% of reserve comparisons.

---

## Resolved since the previous revision

**`filing.period_end` was NULL on 2,404 of 3,679 filings.** It is now NULL on
zero. That was the single largest cause of the reserve shortfall: a reserve row
that does not date itself falls back to the filing's period, and Diamondback's
FY2025 10-K went from 0 readings to 48 on that field alone.
`scripts/backfill_filing_metadata.py` recovered 1,952 from cached submissions
payloads and 452 from older paginated blocks.

**The reserve category patterns mislabelled a standard heading.** "Proved
Developed and Undeveloped Reserves" is the *total* — it is what the standard
XBRL element is named after — and it matched the developed pattern, so
Diamondback's FY2025 total collided with its real developed figure and the
conflict rule discarded both. A disclosure whose identity closes exactly became
three holes.

**The standardized measure had no extractor.** It now has one, and it is the
best-agreeing of the three at 95.9%.

## Still outstanding from the previous revision

**`document.kind` is still wrong.** It is derived by matching a document's name
against `filing.primary_doc`, which was NULL for two thirds of filings when the
corpus was last indexed. The split still reads 3,002 exhibits to 1,267
primaries. `filing.primary_doc` is now populated, so this is one re-run of
`scripts/index_documents.py --reindex` away — it has not been done, and any rule
that prefers primary documents over exhibits is still reading a bad field.

---

## Problem 1 — the majors' layouts

**The largest single cause across every remaining column.**

| Column | Located, no fact |
|---|---|
| Production cost per unit | 32 |
| Proved reserves | 32 |
| Average realized price | 26 |
| Standardized measure | 17 |

The names recur: BP, Chevron, ConocoPhillips, Shell, Equinor, Petrobras, Eni,
Ecopetrol, Suncor, Cenovus, Imperial Oil, TotalEnergies, ExxonMobil, Woodside,
YPF, CNQ. The index finds their disclosure and the parser reads nothing from it.

Three distinguishable layouts, in rough order of how many filers each blocks:

- **Geography-segmented tables.** Columns are regions and the year is stated
  once, sometimes as a block heading inside the body. The cash-flow extractor
  handles this now (`_segmented`, and a bare date row opening a block) and the
  reserve and production extractors do not. Porting it is the single highest-
  value piece of work on this list, because the same layout blocks the same
  filers in three columns.
- **Nested column axes.** Evolution Petroleum's S-K 1204 table is year ×
  (Volume, Price): six numeric columns against three years, so every row fails
  the "values line up with the year columns" check. No extractor handles this.
- **NI 51-101 categories.** The 40-F filers report proved, probable and
  proved-plus-probable at forecast prices. `_CATEGORY_PATTERNS` has no
  proved-plus-probable entry and the developed + undeveloped = total identity
  has no counterpart, so this needs its own category set and its own check —
  and the values it produces are **not comparable** to a Subpart 1200 filer's,
  which the panel knows via `disclosure_regime` and the extractor does not.

## Problem 2 — segment versus consolidated

```
table choice: only one table                                232
table choice: several tables disagree, nothing to choose on  206
table choice: no table reconciles to revenue                 46
table choice: reconciles to reported revenue                 41
table choice: identical tables                               15
table choice: reconciles to reported production              13
```

A filing prints the production table for the company and again per segment or
field, and every one passes the internal BOE identity, so nothing inside the
document separates them. Two external referents are now used — volume × price
against reported revenue, and total volume against reported production — and
206 documents still cannot be decided.

**A narrower version of this was costing more than the problem itself.** The
gate is about scope, which is a question about volume and price; a per-unit cost
table states a rate and has no scope. Dropping the whole document when the
volume tables could not be told apart discarded cost rows that were never in
question, for twelve filers. Cost is now kept when the document states it once.
The same reasoning has not been applied to anything else, and probably should
be: the gate should ask which concepts a given ambiguity actually touches.

Two signals remain unused: `document_line.section` (the consolidated table is in
Item 2 or Item 7; segment tables sit under a named-region heading) and table
position (the consolidated table is almost always first).

## Problem 3 — rows the filter refuses

For cost, 16 filers have a table that parses with no row surviving the
lifting-cost filter — PBR, EC, COP, EGY, BKV, OVV among them. The filter exists
because a unit-cost section lists depletion, overhead, taxes and midstream
alongside the lifting cost, and taking every row would put DD&A in a
production-cost column at half again the real value. It is currently a
whitelist of lifting-cost phrasings plus a blacklist of everything else, and the
16 are filers whose phrasing is in neither.

## Problem 4 — filers the locator never reaches

Small and shrinking. Two filers have no reserve-table language (SKYQ, TPET),
six have no standardized-measure language (KGEI, SKYQ, TBN, TPET, TTE, VET).

Sky Quarry and Trio Petroleum are near-shells; Sky Quarry's cohort membership is
the weakest entry in `SIC_OVERRIDES`. Black Stone Minerals and Texas Pacific
Land appear in the cost and price residuals and should not: they are
non-operators, hold royalty interests, report no lifting cost, and a blank there
is the business model. The coverage report should exclude non-operators from
those columns rather than counting them as missing.

---

## Ranked by value per unit of work

| | Work | Unblocks |
|---|---|---|
| 1 | Port the cash-flow extractor's segmented and stacked layout handling to reserves and production | up to 32 filers across three columns |
| 2 | `index_documents.py --reindex` to correct `document.kind` | nothing directly; unblocks any primary-versus-exhibit rule |
| 3 | Section and position signals for table choice | up to 206 documents |
| 4 | Widen the lifting-cost row filter | 16 filers |
| 5 | Add volume terms to the production locator's query | measurement only — makes one row of this document meaningful |
| 6 | NI 51-101 category set and identity | 6–8 filers, with a regime badge |
| 7 | Nested column axes (year × metric) | a handful, Evolution among them |

Still true, and worth repeating: nothing on this list needs a language model.
Every item is a pattern set, a signal already in the store, or a re-run.

## What this document does not cover

- **Currency.** These counts ask whether a column can be filled at all, not
  whether the value is current. A filer whose last figure is FY2012 counts as
  covered here and is stale in the panel.
- **Verification.** Table-read rows carry a source span by construction and
  should pass verification at a higher rate than XBRL rows. Unmeasured.
- **The 18 filers with no revenue or capex.** A different gap with an XBRL-side
  cause. One registry alias —
  `ResultsOfOperationsRevenueFromOilAndGasProducingActivities` — reaches five of
  them and takes effect on the next `ingest_xbrl` run, which has not been done.
  A Results-of-Operations *extractor* was measured and rejected: the note is
  locatable for only 3 of the 20.
