# Panel gaps: why locatable is not yet extracted

**Scope.** The 89 producer-verified cohort members and the seven panel columns that
come from filing tables. Revenue and capex are excluded — XBRL covers both at 71/89
and neither has a table extractor.

Every number below was measured against the corpus on disk, not estimated.

---

## Where the three totals stand

| Panel column | In XBRL | Locatable | Extractable today | Shortfall |
|---|---|---|---|---|
| Proved developed reserves | 50 | 86 | 24 | **62** |
| Total proved reserves | 49 | 86 | 24 | **62** |
| Proved undeveloped reserves | 49 | 86 | 24 | **62** |
| Standardized measure | 47 | 87 | 0 | **87** |
| Production volume | 37 | 68 | 47 | **21** |
| Average realized price | 8 | 68 | 47 | **21** |
| Production cost per BOE | 2 | 68 | 47 | **21** |

*Locatable* — the full-text index finds the disclosure's language in an annual report
on disk. *Extractable today* — running the **table extractor** over every located
document of that filer's most recent annual filing returns at least one reading; this
counts the document path only, which is why it sits below the XBRL column for reserves.
*Shortfall* — locatable minus extractable: filers whose disclosure the index can point
at and the extractor cannot yet read.

The reserve gap is the larger one and it is not a parsing problem. Most of it is a
single missing field in a different table.

---

## Problem 1 — `filing.period_end` is NULL on 65% of filings

**Blocks 34 of the 62 reserve failures. Largest single cause, and unrelated to
document parsing.**

```
filing.period_end NULL:  2,404 / 3,679   (883 of them 10-Ks)
filing.primary_doc NULL: 2,404 / 3,679
```

A reserve table row often names its own period ("As of December 31, 2025"), but where
it does not, the extractor needs the filing's fiscal period end as a fallback.
`reserve_readings(raw, fallback_period=None)` returns nothing for a table whose rows
are undated, so the filing metadata decides whether a perfectly parseable table yields
anything at all. Diamondback is the clean demonstration:

```
fallback_period=None          ->  0 readings
fallback_period='2025-12-31'  -> 48 readings
```

The cause is in `record_filing`: filings are registered from fact rows, and a fact row
carries an accession, a form and a filing date but no period end and no primary
document name. Anything registered by the XBRL path therefore has both fields NULL
forever, and nothing backfills them. The submissions API returns `reportDate` and
`primaryDocument` alongside every filing, so this is a backfill, not a fetch.

The same NULL corrupts `document.kind`: `index_documents.py` labels a document
`primary` only when its name matches `filing.primary_doc`, so for the 2,404 filings
where that field is NULL every document is labelled `exhibit` — including
`fang-20251231.htm`, `eog-20251231.htm` and `dvn-20251231.htm`, each of which is that
filer's 10-K and the only document in its accession. The corpus currently reads 3,002
exhibits to 1,267 primaries, and the split is not trustworthy. Any future rule that
prefers primary documents over exhibits is reading a field that is right roughly a
third of the time.

**Fix:** backfill `period_end` and `primary_doc` from the cached submissions payloads,
which are already on disk for all 89 filers. No network, no re-parse. Re-run
`index_documents.py --reindex` afterwards to correct `kind`.

---

## Problem 2 — foreign table conventions

**Blocks 16 of the remaining 28 reserve failures and 8 of the 21 production failures.**

| Regime / taxonomy | Reserve failures | Production failures |
|---|---|---|
| `subpart-1200` / `ifrs-full` (20-F) | 9 | 8 |
| `ni-51-101` / `ifrs-full` (40-F) | 7 | 0 |

Named: BP, Shell, Equinor, Eni, Petrobras, TotalEnergies, YPF, Woodside, Ecopetrol,
GeoPark, Vista (20-F); Suncor, Cenovus, CNQ, Vermilion, Obsidian, Greenfire, Kolibri
(40-F).

These filers are located reliably — the index finds reserve-table language in their
documents, mostly in exhibits — but the extractor reads none of them. Two distinct
causes, worth separating because only one is cheap:

- **NI 51-101 filers (40-F) print a different table.** The categories are proved,
  probable and proved-plus-probable rather than developed/undeveloped/total, evaluated
  at forecast prices. `_CATEGORY_PATTERNS` has no proved-plus-probable entry, and the
  `developed + undeveloped = total` gate has no counterpart for that schema. This needs
  its own category set and its own identity, not looser patterns — and the values it
  produces are **not comparable to a Subpart 1200 filer's**, which the panel already
  knows via `disclosure_regime` but the extractor does not.
- **20-F filers print a Subpart 1200 table in prose-heavy layouts.** The majors do not
  abbreviate units — ExxonMobil heads columns "(millions of barrels)" — which
  `reserves.py` already handles, so the failure here is likelier to be table structure
  (multi-level headers, region-segmented rollforwards) than vocabulary. Worth sampling
  three before writing anything.

---

## Problem 3 — the reserve category patterns mislabel a standard heading

**Fixed in this pass; recorded because the failure mode is not obvious and will recur
for other categories.**

`Proved Developed and Undeveloped Reserves` means **total** proved — it is the phrase
the standard XBRL element is named after (`ProvedDevelopedAndUndevelopedReserveNet`)
and it is how most filers head the rollforward table. The `developed` pattern matched
it, so the rollforward's total was read as developed reserves.

The consequence was not a wrong number but a missing one, which made it hard to see.
Diamondback FY2025 produced two conflicting values for `proved_developed_reserves_boe`
— 3,617,856 MBoe from the mislabelled rollforward and the real 2,521,028 from the table
that actually says "proved developed" — so the ingest's "two readings disagree, drop
the cell" rule discarded both, *and* total proved never appeared at all. A disclosure
whose identity closes exactly (2,521,028 + 1,096,828 = 3,617,856) became three holes.

The gate did not catch it. `closes()` reported "kept on the developed/undeveloped pair
only" for every period, because a developed and an undeveloped figure were both present
— they were simply the wrong figures.

**Lesson for the remaining columns:** an arithmetic gate tests that the numbers are
consistent, not that they were labelled correctly. Two mislabels that preserve the
identity pass it.

---

## Problem 4 — segment tables cannot be told from consolidated without external evidence

**Refuses 183 of 692 located production documents.**

```
table choice: only one table                                211
table choice: several tables disagree, no revenue to choose  183
table choice: reconciles to reported revenue                  35
table choice: no table reconciles to revenue                  39
table choice: identical tables                                 8
```

A filing prints the S-K 1204 table for the company and again per segment, region or
field. Every one of them passes the internal BOE identity — Talos's Gulf of Mexico
table is exactly as self-consistent as its consolidated table — so nothing inside the
document distinguishes them. The current rule reconciles total volume × realized price
against XBRL revenue (Talos: $891M vs $369M vs $152M, consolidated wins) and refuses
where revenue is unavailable.

Refusing is correct — a segment table stored as company production is a wrong number —
but 183 documents is a large price. Three cheaper signals are available and none is
yet used:

1. **Section.** `document_line.section` is populated for every indexed line. The
   consolidated table is in Item 2 or Item 7; segment tables usually sit under a
   named-region heading. The locator already carries `ReserveSite.section` and nothing
   reads it.
2. **Position.** The consolidated table almost always comes first.
3. **Reserve volumes as the referent.** Production volume must be consistent with the
   reserve rollforward's production line, which the reserve extractor already reads.
   That gives a consolidated referent for filers with no XBRL revenue.

---

## Problem 5 — filers the locator never reaches

**13 filers for production, 3 for reserves.** These are not extraction failures; the
FTS query finds no matching language in any annual report on disk.

| | Filers |
|---|---|
| No S-K 1204 language | ANNA, BSM, CVE, EP, EPSN, GFR, IMO, KGEI, SKYQ, SU, TBN, TPET, VET |
| No reserve-table language | SKYQ, TPET, TPL |

Three groups, and only the first is a real gap:

- **Genuinely differently worded.** Imperial Oil and Suncor are integrated filers whose
  production disclosure sits inside segment reporting rather than a standalone S-K 1204
  table. Cenovus, Vermilion, Greenfire and Kolibri are 40-F filers whose tables are in
  an Annual Information Form that may not be in the corpus at all — worth checking
  before assuming a vocabulary problem.
- **Correctly absent.** Black Stone Minerals and Texas Pacific Land are non-operators.
  They hold royalty interests, report no lifting cost and no production cost per BOE,
  and a blank cell is the business model rather than a gap. The panel already flags
  this with `is_operator`; the coverage report should exclude them from these columns
  rather than counting them as missing.
- **Marginal filers.** AleAnna, Trio Petroleum, Sky Quarry and Tamboran are pre-revenue
  or near-shell. Sky Quarry's cohort membership is itself the weakest entry in
  `SIC_OVERRIDES`.

---

## Problem 6 — standardized measure has no extractor at all

**XBRL 47/89, locatable 87/89, extracted 0.**

The standardized measure of discounted future net cash flows is disclosed in the same
supplemental note the reserve rollforward sits in. Searching the index directly for
`"standardized measure" OR "future net cash flows"` finds it in an annual report for
**87 of the 89 producers** — the widest reach of any column measured here. It is a single dollar figure per period with no product axis — by
some distance the simplest of the remaining columns — and it has the strongest possible
self-check, because the note prints the full build-up (future cash inflows, less
production costs, less development costs, less taxes, discounted at 10%) and the
components must reconcile to the total.

It is unstarted rather than blocked. Given the locator exists, this is the cheapest
remaining column.

---

## Ranked by value per unit of work

| | Work | Unblocks |
|---|---|---|
| 1 | Backfill `period_end` / `primary_doc` from cached submissions | up to 34 filers × 3 reserve columns |
| 2 | Standardized-measure extractor | up to 40 filers × 1 column |
| 3 | Section + position signals for table choice | up to 183 documents |
| 4 | NI 51-101 category set and identity | 7 filers × 3 columns, with a regime badge |
| 5 | Sample three 20-F reserve tables before writing anything | 9 filers × 3 columns |

Nothing on this list needs a language model. Every item is a metadata backfill, a
pattern set, or a signal already present in the store and not yet read — which is worth
saying plainly, because "the extraction layer" has been carrying an implied assumption
that it means LLM extraction, and so far it has not.

---

## What this document does not cover

- **Currency.** These counts ask whether a column can be filled at all, not whether the
  value is current. A filer whose last tagged reserve figure is FY2012 counts as
  covered here and is stale in the panel.
- **Verification.** An extracted figure still has to be located verbatim in the document
  it cites. Table-read rows carry their source span by construction, so they should pass
  at a higher rate than XBRL rows, but that has not been measured.
- **The 18 filers with no revenue or capex.** A different gap with a different cause,
  and XBRL-side rather than document-side.
