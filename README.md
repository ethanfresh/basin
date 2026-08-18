# Basin

**Peer-comparable financial data for US oil & gas producers, extracted from SEC filings, with a citation behind every number.**

Basin builds and maintains a structured dataset of the operating metrics that matter for exploration & production companies — reserves, production, realized prices, per-barrel costs, capex, and management guidance — pulled directly from SEC filings as they are published. Every value in the dataset carries the accession number, section, fiscal period, and verbatim source text it came from, so any number can be spot-checked against the original document in one click.

It is built for people who need this data and cannot justify an enterprise terminal seat: smaller investment firms, lenders, consultants, corporate development teams, and accounting firms.

> **Status: Facts layer running, with a dashboard over it.** The XBRL client and fact store are built and have ingested the full SIC-1311 cohort — 94 companies, 5,650 facts, 805 cited filings — served by a read-only web dashboard. The extraction, derivation, change-detection and delivery layers are not started. See [Roadmap](#roadmap).
>
> *The name `Basin` is a working title — chosen because it is vertical-specific and avoids the collision with the unrelated "FinAgent" system from Zhang et al., KDD '24.*

---

## Table of contents

- [The problem](#the-problem)
- [What Basin does](#what-basin-does)
- [Background: how SEC filings work](#background-how-sec-filings-work)
- [Why one industry](#why-one-industry)
- [Feasibility: verified against live SEC data](#feasibility-verified-against-live-sec-data)
- [The metrics](#the-metrics)
- [Architecture](#architecture)
- [Comparability](#comparability-the-core-claim-and-the-core-liability)
- [Scope of the first version](#scope-of-the-first-version)
- [Roadmap](#roadmap)
- [Working principles](#working-principles)

---

## The problem

Every US public company is legally required to file detailed reports with the SEC. Those reports are free and public, in a database called EDGAR. Everything an investor is entitled to know is in there.

The difficulty is that it is buried in hundreds of pages of prose and tables, in formats designed for reading rather than for analysis. Comparing forty companies means a person reading forty documents and typing numbers into a spreadsheet — then doing it again next quarter.

Tools that solve this exist. They cost tens of thousands of dollars per seat per year, and they are priced and sold for large institutions.

## What Basin does

Basin narrows the problem to one industry and solves it completely for that industry.

- **Peer comparison tables** — 20+ producers side by side on the same metrics, for the same periods.
- **"What changed" reports** — the newest filing diffed against the prior one, with material changes surfaced and explained.
- **Alerts** — when guidance moves, when a cost metric shifts, when management changes how it defines or reports something.
- **Excel export** — with citations preserved, because the output has to survive being forwarded to someone skeptical.

The design constraint that governs everything: **retrieval is a recall mechanism, not a precision mechanism.** Similarity search is good at finding the paragraph that probably discusses a topic. It is not good enough to populate a spreadsheet cell. Anything destined for a cell comes from a typed extraction carrying a source span that has been verified to appear verbatim in the cited document.

## Background: how SEC filings work

For readers new to this domain:

| Filing | What it is |
|---|---|
| **10-K** | The annual report. Comprehensive, hundreds of pages, audited. |
| **10-Q** | The quarterly version. Shorter, unaudited. |
| **8-K** | A "material event just happened" announcement, filed as needed. Quarterly earnings releases arrive as an attachment to one of these, labeled EX-99.1. |

Alongside the human-readable filing, companies also submit the same figures as **XBRL** — a structured, machine-readable representation where each number carries a standard label, a unit, and a fiscal period. The SEC exposes this through free APIs with no key required, notably `companyfacts`.

XBRL matters enormously here. A number read from XBRL is exact, comes with its originating accession number, and involves no language model at all — which structurally eliminates the possibility of a fabricated figure. Basin takes everything it can from XBRL, and only falls back to document extraction for what XBRL does not cover.

## Why one industry

This is a technical constraint before it is a go-to-market decision.

Open-ended extraction — "pull any metric from any company" — has no ground truth. It cannot be measured, so it cannot be improved, and a customer has no basis on which to trust it.

Fixing the industry and the metric set converts the problem into a closed one: **N known companies × K known fields × known periods.** That grid can be labeled by hand, once. A hand-labeled answer key makes per-field precision and recall measurable, which makes regression testing in CI possible, which makes the system improvable and its accuracy claims verifiable.

The evaluation harness is not overhead around the product. It is the product's core asset.

## Feasibility: verified against live SEC data

Two questions were checked against the live SEC APIs before committing to oil & gas.

### 1. Is the filer population large enough, and is it actually on EDGAR?

This is what disqualified the originally-considered vertical, copper: most copper producers are Canadian and file with Canadian regulators, not the SEC. A product promising sector coverage would silently miss much of the sector.

Oil & gas has no such gap. Querying EDGAR for SIC code 1311 (crude petroleum & natural gas):

| Measure | Count |
|---|---|
| CIKs that have ever filed a 10-K | 1,380 |
| Currently listed with a ticker | 115 |
| **Filed a 10-K since January 2025** | **86** |
| Of those, with a foreign business address | **2** |
| Of those, that also file 8-Ks | **86** |

Universal 8-K coverage matters because production and capex guidance is announced in earnings releases, not annual reports — so 8-K exhibit ingestion is a hard prerequisite rather than a later enhancement.

The 86 includes royalty trusts, minerals companies, midstream, refiners, oilfield services misclassified into the SIC, and a small amount of noise — a biotechnology company is currently filed under this code. **Actual E&P operators number roughly 40–45**, which is ample for a 20-company first version with real basin cohorts inside it.

### 2. Are the key metrics already tagged in XBRL?

Partially, and inconsistently. Sampling ten major E&Ps for the relevant reserve and cost concepts:

| Concept | Filers tagging it |
|---|---|
| `srt:ProvedDevelopedReservesBOE1` | 6 / 10 |
| `srt:StandardizedMeasureOfDiscountedFutureNetCashFlows…` | 6 / 10 |
| `srt:ProvedDevelopedAndUndevelopedReserveProductionEnergy` | 5 / 10 |
| `srt:AverageSalesPrices` | **2 / 10** |
| `srt:ConsolidatedOilAndGasProductionCostsPerUnitOfProduction` | **1 / 10** |

Two of the ten sampled companies expose essentially no reserve concepts at all. One tags the `us-gaap:` variants rather than the `srt:` ones, so the ingest client needs namespace aliasing rather than a flat concept list.

The pattern is unfavorable in a specific way: the **per-unit economics** — realized price per barrel, production cost per barrel — are the most commercially valuable figures and the least reliably tagged.

#### Measured across the whole population

The sampling above was ten companies. The client now measures all of them. Re-running the filer census reproduces the original figures — **1,380 CIKs** have ever filed a 10-K under SIC 1311, **2** have a foreign business address, and **8-K coverage is universal (100/100)**, confirming that 8-K exhibit ingestion is a hard prerequisite.

| Measure | Count |
|---|---|
| CIKs that have ever filed a 10-K | 1,380 |
| Filed a 10-K since January 2025 | 101 |
| Distinct issuers after collapsing successor CIKs | **100** |
| Of those, currently ticker-listed | 86 |
| Of those, with a foreign business address | 2 |
| Of those, that also file 8-Ks | 100 |

Coverage of the six Facts-layer concepts, across all 100 issuers:

| Concept | Current (period ≥ 2023) | Ever tagged |
|---|---|---|
| Capex | 49 / 100 | 69 / 100 |
| Standardized measure | 48 / 100 | 61 / 100 |
| Proved developed reserves | 31 / 100 | 41 / 100 |
| Total proved reserves | 31 / 100 | 43 / 100 |
| Proved undeveloped reserves | 30 / 100 | 42 / 100 |
| Oil & gas revenue | 24 / 100 | 64 / 100 |
| Production volume | 23 / 100 | 28 / 100 |
| **Average realized price** | **2 / 100** | 8 / 100 |
| **Production cost per BOE** | **0 / 100** | 4 / 100 |

Three findings that change how the cohort gets picked:

- **Tagged is not the same as current.** Filers tag a concept and then stop. EOG's reserve concepts end at FY2021; Matador's at FY2012; Murphy's total proved at FY2018. Counting "ever tagged" overstates usable coverage by about a third across the population, so currency is scored separately.
- **Some of the largest producers tag no reserve data at all.** ConocoPhillips's entire `srt` namespace is two tags; Occidental has no `srt` namespace. This is absence, not inconsistency — company size is anti-correlated with XBRL completeness often enough that the cohort cannot be picked by market cap.
- **Ranking by coverage alone selects the wrong companies.** All five issuers with a perfect 6/6 are royalty, minerals, or partnership vehicles — Black Stone Minerals, Sitio Royalties, TXO Partners — not operators. They tag cleanly because their disclosures are simpler, which is exactly why they are not the product. The operator filter has to come before the coverage ranking, not after.

At the population level the per-unit economics gap is no longer merely unfavourable, it is close to total: **realized price is current for 2 filers in 100, and production cost per BOE for none.** Both are mandated disclosures under Regulation S-K Subpart 1200. The entire commercial value of those two fields sits behind document extraction, which is the clearest possible statement of where the work is.

**This narrows the free path without undermining the thesis.** SEC Regulation S-K Subpart 1200 and ASC 932 require these disclosures to appear in every 10-K, in defined terms. The data is unambiguously present in the documents; it is simply not always machine-readable. That yields the thing the architecture actually depends on — **regulator-fixed definitions to label a golden set against** — while leaving the extraction work as real work. The gap between "the SEC requires this disclosed" and "nobody tagged it" is precisely the gap a terminal subscription is currently charging to close.

## The metrics

Eight fields for the first version, grouped by which layer produces them, because the layer determines how each is evaluated.

### Facts layer — XBRL, exact match

| Field | Plain meaning |
|---|---|
| Proved reserves (developed / undeveloped, by product) | Oil and gas confirmed to be in the ground and profitably extractable |
| Standardized measure / PV-10 | The SEC's standardized present-value estimate of those reserves |
| Annual production volumes by product | How much was actually produced |

### Extraction layer — schema-constrained LLM, mandatory source span

| Field | Plain meaning |
|---|---|
| Average realized price per unit | What the company was actually paid per barrel or Mcf — reported both before and after hedging |
| Production cost (LOE) per BOE | Cost to lift one barrel out of the ground; realized price minus this is roughly the unit margin |
| Cash G&A per BOE | Corporate overhead allocated per barrel |
| Capex, actual and guided | Spending on new drilling |
| Production guidance range | Management's own forecast for the coming period — **sourced from 8-K EX-99.1, not the 10-K** |

### Derivation layer — pure Python, unit tested

Reserve life (R/P ratio), reserve replacement ratio, finding & development cost per BOE, unit margin per BOE.

*BOE — "barrel of oil equivalent" — is the industry's common unit for adding oil and natural gas volumes into a single figure.*

## Architecture

Basin is a **pipeline, not an agent.** "Alert me when guidance changes" cannot be answered at request time. The system maintains a panel dataset — companies × metrics × periods — that rebuilds itself as filings arrive. A language model appears in exactly two places: as a schema-constrained extractor at ingest, and as an explainer at read time. Chat is one surface over the table, not the system itself.

| Layer | Mechanism | How it is evaluated |
|---|---|---|
| **Watch** | EDGAR daily index / submissions polling → filing events | liveness |
| **Facts** | XBRL `companyfacts` → typed fact rows | exact match |
| **Extraction** | Per-vertical KPI schema; LLM constrained to schema; mandatory source span | labeled golden set, per-field P/R |
| **Derivation** | Pure Python — growth, ratios, unit normalization | unit tests |
| **Change detection** | Diff facts, extractions, and narrative sections against prior, with materiality thresholds | precision on alerts |
| **Delivery** | Tables, Excel export, alerts, chat | LLM judge |

Two consequences worth stating explicitly:

- **The system of record is relational, not a vector store.** Facts are typed rows with provenance, append-only and versioned by accession number. Restatements and 10-K/A amendments must not silently overwrite the history that citations depend on. Vector search serves the narrative layer only.
- **False alerts are the failure mode that kills trust.** Change detection is evaluated on precision, not recall. An alert that turns out to be a rounding artifact costs more than a missed one.

## Comparability: the core claim and the core liability

Putting forty companies in one table asserts that the rows are comparable. Frequently they are not.

Two producers can both report "production cost per barrel" and mean materially different things — one including gathering and transportation charges, the other reporting them below that line. The same applies to realized prices quoted before versus after hedging.

**Design decision: definition mismatch is a first-class field on the cell, not a footnote.** A table that says "these four report on a different basis, and here is the difference" is trustworthy. A visually clean table that quietly mixes bases is the thing that loses an account.

The first live instance of this turned up in the Facts layer, before any language model was involved:

- **Products collide inside one concept.** XBRL dimensions realized price by product, but the `companyfacts` API flattens the dimension away. Matador's oil and gas prices arrived as two values on one concept, distinguishable only by unit (`USD/bbl` vs `USD/MMBTU`). Product is therefore part of a cell's identity — oil price and gas price are separate rows, not competitors for one cell.
- **A filer's own unit label can be wrong.** Devon tags total proved reserves as `MMBoe` through FY2022 and `MMcfe` from FY2023, while the values run continuously (2182 → 1817 → 2155). A genuine BOE-to-cfe change would move the figure roughly sixfold; their *developed* reserves stay in `MMBoe` at a comparable magnitude in the same filings. The later unit label is simply incorrect.

- **The declared unit does not determine the magnitude.** Filers disagree about whether the tagged value already has its unit's prefix applied. Diamondback reports proved developed reserves as `2,521,028,000` tagged `MBoe` — base units under a presentational label, since their reserves are ~2.5 billion BOE, not 2.5 trillion. Devon reports `2,155` tagged `MMcfe`, where the value *is* scaled to the label. Both are internally coherent; neither is inferable from `(value, unit)` alone.

- **Alias choice is a measurement, not a default.** Which tag a filer means by "total proved reserves" is decided per filer by testing an identity that has to hold in every filing — developed + undeveloped = total — across every combination of the tags that filer actually uses, keeping the combination whose numbers agree. 33 of 94 validate, 59 lack all three concepts, and 2 *drifted*: the identity held for years and then stopped, because the tag kept its name and changed its meaning. Continental holds for 8 of 13 periods and Murphy for 5 of 8, in both cases failing the most recent. Recency is scored separately from frequency, since a panel showing FY2025 is not helped by agreement in FY2012.

- **A taxonomy migration silently truncates history.** Filers move the same tag name from `us-gaap:` to `srt:` mid-history. Reading only the first match dropped everything before the move — Continental's developed reserves began at 2016 instead of 2011. Merging the two halves recovered **620 reserve rows across 25 filers**, and the store grew from 5,650 facts to 7,673.

- **A missing alias reads exactly like a missing disclosure.** The registry had `ProvedUndevelopedReserveBOE` but not `ProvedUndevelopedReserveBOE1`, which a sweep of 40 filers found in 14 of them against 3 for the form it did have — plus singular/plural spellings and `srt:`/`us-gaap:` mirrors throughout. Undeveloped reserves read as 5/100 coverage because of the gap in the registry, not because filers were not tagging it. Coverage numbers measure the registry as much as the filers, which is an argument for checking them against the raw payloads rather than trusting them.

- **The arithmetic does not always close.** Two identities have to hold inside one filer's own numbers: developed ≤ total, and developed + undeveloped = total. Across the cohort **85 of 251 company-periods fail** — 63 quote the concepts in different units, 12 report developed as exactly equal to total, and 10 have components that disagree with the stated total.

  The sum check is the one that earns its place, because it localises the fault. Antero's components sum to **17,261 MMcfe** against a total reading **17,261,000** — identical digits, a factor of a thousand apart. Continental's components sum to 2.68 million MBoe against a total of 745 thousand; the two components agree with each other, so it is the total's tag (`ProvedDevelopedAndUndevelopedReserveNetEnergy`) that does not mean what the registry assumed.

Basin does not rewrite a filer's unit — inventing a corrected label is worse than reporting the filer's own. The `unit_discontinuity` view surfaces every series whose declared unit changes, and the dashboard groups a peer table by declared unit so it never renders a ranking it cannot support. **Cross-unit normalisation of volume concepts needs per-filer calibration and is deliberately not applied.**

This is the sharpest form of the comparability problem so far, and it lands in the Facts layer — the one that was supposed to be the easy, exact one. XBRL removes the risk of a *fabricated* number; it does not deliver a *comparable* one.

## Scope of the first version

**One vertical, ~20 companies, 8 metrics, 8 quarters of history.** Go all the way through every layer before adding a single additional company.

The artifact that proves the thesis:

1. One peer comparison table in which **every cell links to its exact accession, section, period, and quoted source text.**
2. One "what changed this quarter" report generated from the same store.

If the citations do not survive spot-checking, nothing downstream matters.

## Running it

The SEC requires a declared `User-Agent`; the client refuses to make a request without one rather than sending a default.

```bash
uv venv && uv pip install -e ".[dev]"
export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"

# Enumerate the SIC-1311 population and profile every filer (results cached)
python scripts/discover_cohort.py --out data/cohort_candidates.csv

# Which concepts does a filer actually tag, under which taxonomy, and how recently?
python scripts/coverage_report.py --all-concepts --cik 1090012 --cik 1539838

# Ingest XBRL facts into the store (idempotent per accession)
python scripts/ingest_xbrl.py --cik 1090012 --cik 1539838
python scripts/load_cohort.py          # tickers and cohort metadata

pytest
```

### The dashboard

```bash
uv pip install -e ".[web]"
python -m uvicorn basin.web.app:app --port 8422
```

Four views over the store at `http://localhost:8422`:

- **Panel** — one concept, one period, every company. Rows are grouped by declared unit, because ranking across units would sort by labelling convention rather than size. Every row links to the SEC index page for the accession it came from.
- **Coverage** — the companies × concepts matrix, distinguishing current from stale from never-tagged. The blank space is the extraction layer's mandate, drawn to scale.
- **Companies** — cohort members by how much data each actually has.
- **Data quality** — reserve arithmetic that does not close, unit discontinuities, and fallback tags. Nothing here is silently corrected.

The app opens the store read-only, and every query lives in `basin.store.queries`, so what the browser renders and what the tests assert on are the same code.

## Roadmap

### Done

- [x] **`xbrl_facts`** — rate-limited EDGAR client, concept registry with `srt:` / `us-gaap:` aliasing, typed fact rows, and a per-company coverage report scoring currency as well as presence
- [x] **Fact store schema** — `(concept, value, unit, period, accession, form, extracted_by, source_span)`, append-only, with a `fact_current` view for reads. An LLM-sourced row without a source span is rejected by a `CHECK` constraint rather than by review.

### Next

- [x] Run the coverage report across the full SIC-1311 population — 1,380 CIKs enumerated, 100 distinct current issuers profiled and scored
- [ ] Lock the cohort — filter the 100 to real E&P operators (the coverage ranking alone selects royalty vehicles), then select 20 across basins
- [ ] Filing watcher over EDGAR submissions, so new 10-Ks and 8-Ks trigger ingest
- [ ] Golden set: hand-label the 8 fields × 20 companies × recent periods, starting with the fields XBRL does not cover

### Deferred

8-K item-code filtering (4.02 restatements, 5.02 executive departures, 1.01 material agreements) · Form 4 insider transactions · multi-ticker retrieval · 10-K/A amendment awareness · table-aware extraction, parsing `<table>` to markdown before text extraction so financial tables survive · EDGAR full-text search via `efts.sec.gov` *(endpoint shape unverified — confirm before relying on it)*

## Working principles

- **Nothing is described as working without being run.**
- **Citation spot-checks are part of "done."** A claimed source span must be verified to appear verbatim in the cited document before the extraction counts as correct.
- **Count before committing.** Filer populations and data availability get measured against live APIs, not estimated. The figures in [Feasibility](#feasibility-verified-against-live-sec-data) were produced this way, and the XBRL result corrected an earlier optimistic assumption.

---

## Related

- [`EDGAR-INTELLIGENCE-HANDOFF.md`](EDGAR-INTELLIGENCE-HANDOFF.md) — the session handoff that established this direction, including the reasoning behind separating this from the FinAgent platform repository.

## Data source

All data originates from the SEC's public EDGAR system and its free APIs at `data.sec.gov`. No API key is required. SEC guidelines require a declared `User-Agent` header identifying the requester, and request rates are capped at 10 per second.
