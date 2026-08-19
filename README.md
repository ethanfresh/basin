# Basin

**A consolidated E&P database for US oil & gas producers, built from SEC filings, with a citation behind every number.**

The operating data for exploration & production companies exists, publicly and for free, and it is unusable in the form it is published in. Reserves, production, realized prices, per-barrel costs and capex are scattered across hundreds of pages of prose, tables and inconsistently-tagged XBRL, in 91 separate filers' documents, restated and relabelled from year to year.

Basin consolidates that into one queryable panel — companies × metrics × periods — maintained as filings arrive. Every value carries the accession number, form, fiscal period, page, line and verbatim source text it came from, so any figure can be checked against the original document in one click.

It is built for people who need this data and cannot justify an enterprise terminal seat: smaller investment firms, lenders, consultants, corporate development teams, and accounting firms.

> **Status: Facts layer complete and verified, with a dashboard over it.** 91 companies across two cohorts, 19,729 facts drawn from 3,679 cited filings, and **every current cell checked against the document it cites — 97% located verbatim**. The extraction, derivation, change-detection and delivery layers are not started. See [Roadmap](#roadmap).

---

## Table of contents

- [The problem](#the-problem)
- [What Basin does](#what-basin-does)
- [Background: how SEC filings work](#background-how-sec-filings-work)
- [Why one industry](#why-one-industry)
- [The cohort](#the-cohort-who-is-in-and-how-that-was-decided)
- [Identity](#identity-ticker-and-cik-are-not-interchangeable)
- [The metrics](#the-metrics)
- [Coverage, measured](#coverage-measured)
- [Architecture](#architecture)
- [Comparability](#comparability-the-core-claim-and-the-core-liability)
- [Verification](#verification-a-citation-is-a-claim-until-it-is-checked)
- [Running it](#running-it)
- [Roadmap](#roadmap)
- [Working principles](#working-principles)

---

## The problem

Every US public company is legally required to file detailed reports with the SEC. Those reports are free and public, in a database called EDGAR. Everything an investor is entitled to know is in there.

The difficulty is that it is buried in formats designed for reading rather than for analysis. Comparing forty companies means a person reading forty documents and typing numbers into a spreadsheet — then doing it again next quarter.

Tools that solve this exist. They cost tens of thousands of dollars per seat per year, and they are priced and sold for large institutions.

## What Basin does

Basin narrows the problem to one industry and solves it completely for that industry.

- **A consolidated panel** — every producer, every metric, every period, in one table.
- **Peer comparison** — filers side by side on the same metric for the same period, or on each filer's own latest reported period.
- **Reported history** — what any single company has ever disclosed for a metric, so a number can be read against the filer's own past as well as against its peers.
- **"What changed" reports** — the newest filing diffed against the prior one, with material changes surfaced and explained. *(not started)*
- **Alerts** — when guidance moves, when a cost metric shifts, when management changes how it defines or reports something. *(not started)*
- **Excel export** — with citations preserved, because the output has to survive being forwarded to someone skeptical. *(not started)*

The design constraint that governs everything: **retrieval is a recall mechanism, not a precision mechanism.** Similarity search is good at finding the paragraph that probably discusses a topic. It is not good enough to populate a spreadsheet cell. Anything destined for a cell comes from XBRL or from a typed extraction carrying a source span verified to appear verbatim in the cited document.

## Background: how SEC filings work

| Filing | What it is |
|---|---|
| **10-K** | The annual report. Comprehensive, hundreds of pages, audited. |
| **10-Q** | The quarterly version. Shorter, unaudited. |
| **8-K** | A "material event just happened" announcement. Quarterly earnings releases arrive as an attachment, labeled EX-99.1. |
| **20-F** | The annual report of a foreign private issuer. Carries the same oil & gas disclosure a 10-K does. |
| **40-F** | The annual report of a Canadian issuer under the MJDS. Usually a cover sheet, with the substance attached as exhibits. |

Alongside the human-readable filing, companies also submit the same figures as **XBRL** — a structured, machine-readable representation where each number carries a standard label, a unit, and a fiscal period. The SEC exposes this through free APIs with no key required, notably `companyfacts`.

XBRL matters enormously here. A number read from XBRL is exact, comes with its originating accession number, and involves no language model at all — which structurally eliminates the possibility of a fabricated figure. Basin takes everything it can from XBRL, and falls back to document extraction for what XBRL does not cover.

## Why one industry

This is a technical constraint before it is a go-to-market decision.

Open-ended extraction — "pull any metric from any company" — has no ground truth. It cannot be measured, so it cannot be improved, and a customer has no basis on which to trust it.

Fixing the industry and the metric set converts the problem into a closed one: **N known companies × K known fields × known periods.** That grid can be labeled by hand, once. A hand-labeled answer key makes per-field precision and recall measurable, which makes regression testing in CI possible, which makes the system improvable and its accuracy claims verifiable.

The evaluation harness is not overhead around the product. It is the product's core asset.

## The cohort: who is in, and how that was decided

**91 companies: 75 Oil & Gas E&P, 16 Oil & Gas Integrated.**

Membership is not a hand-written list and not an SIC code. SIC 1311 sweeps in midstream companies, refiners, royalty trusts, oilfield services, and — observed in the population — a biotechnology company. The earlier filter matched substrings against company names (`"royalt"`, `"midstream"`, `"pipeline"`), which cannot tell a royalty vehicle from an operator when the name does not say so.

Cohort now comes from **Finviz's industry classification**, restricted to the two industries whose companies produce hydrocarbons and therefore own reserves. Drilling and Equipment & Services sell services to producers; Midstream gathers and fractionates third-party volumes under fee contracts, booking throughput rather than reserves; Refining buys crude and sells products. None of them have a reserve base, a lifting cost or a production volume — which is to say none of them have the metrics this schema is built out of.

**A cohort is a KPI schema, not a label.** Comparison is legal within one and forbidden across, because putting an E&P and a pipeline in one reserves table asserts a comparability that does not exist. The panel enforces this rather than leaving it to the reader.

### The classification is good, and it is not clean

Two securities Finviz places in a producing industry are not energy companies at all, and three more do not produce. Each was caught by a test a non-producer fails by definition — a company that lifts hydrocarbons has a reserve base and says so, either in tagged concepts or in its annual report:

| Excluded | Why |
|---|---|
| `MHM` | A Bank of America structured note, filed under Oil & Gas Equipment & Services |
| `TGS` | Transportadora de Gas del Sur — an Argentine gas pipeline. **0 reserve-language hits in 1.27M characters** of its 20-F; tags only revenue and capex |
| `SLNG` | Stabilis Solutions — small-scale LNG distribution, not upstream. 0 hits |
| `VIVK` | Vivakor — oilfield waste remediation and crude transport. 0 hits |
| `NRT` | North European Oil Royalty Trust — passive royalty on German concessions. 4 hits, all risk-factor prose, against **30–90 for every other royalty trust in the cohort** |
| `CKX` | CKX Lands — a Louisiana land lessor. Its 10-K states the position outright: reserve information *"is not available. A schedule indicating such reserve quantities is, therefore, not presented."* |

The check runs over the whole cohort and reports three verdicts. `unknown` — nothing was available to test — is deliberately distinct from `non-producer`, which means the filing was read and holds no reserves. Current standing: **89 producers, 5 non-producers (all excluded), 3 untested shells.**

Royalty and minerals vehicles like Dorchester, Black Stone and Texas Pacific Land **pass**, because they genuinely own reserves even though they operate nothing. That is a different distinction from `is_operator`, and both are kept.

Excluding a company clears its membership and nothing else. Its facts, filings and citations stay exactly as they were — the store is append-only, and being out of scope is not a reason to destroy history that other rows cite.

## Identity: ticker and CIK are not interchangeable

Basin **presents by ticker and keys by CIK**, because the two identifiers fail in opposite directions.

A CIK is assigned once and never reused. A ticker is released when a company delists and can later be reassigned to an unrelated filer — so keying facts on one would let two companies' histories merge silently, which is the failure an append-only store exists to prevent. Ticker is the identity in the panel, the URL and the export; it is never the thing a fact points at. A partial unique index makes a collision impossible rather than unlikely.

`NULL` is meaningful: it means the filer has no current listing, not that the ticker is unknown. **14 companies in the store are in that state** — taken private, acquired, or delisted — and they still file, still carry facts, and still have to be citable. Continental Resources went private in 2022 and keeps filing 10-Ks because of its public debt.

> **A known limitation follows from this.** Cohort membership comes from a screener, and a screener lists traded securities, so every one of those 14 is outside the cohort by construction — including producers that still file. Defining the cohort from a classification is a large improvement on guessing from names, and it draws this boundary as a side effect. Closing it means adding a second membership path for filers with no listing, which is not built.

### Following a change of registrant

A company can replace the legal entity that files with the SEC without changing what the business is. EDGAR assigns the new entity its own CIK and its own file number, the SEC's ticker map follows the symbol immediately, and the filing history does not move.

`XOM` resolves to CIK `2115436` — ExxonMobil Holdings Corp, which has filed 10-Qs and nothing else. Every 10-K is on CIK `34088`. Reading the new CIK does not fail; it returns nothing, which is worse, because nothing looks like a filer that does not tag its reserves.

The link is established from evidence, not from matching names. Rule 12g-3(a) requires the successor to register on **Form 8-K12B**, so the presence of that form is what identifies a succession at all; that filing names the predecessor in its explanatory note; and the name is confirmed against EDGAR's company search restricted to filers that have actually submitted a 10-K. All three must agree. Two candidates whose 8-K12B yielded only a defined term ("the Trust", "Penn West") are recorded **unconfirmed** rather than guessed at.

The ticker then moves to the registrant holding the history, because a display identity should point at the row with data behind it. Cohort membership follows the same link, so the empty successor does not double-count as a second company.

## The metrics

Nine fields, grouped by which layer produces them, because the layer determines how each is evaluated.

### Facts layer — XBRL, exact match

| Field | Plain meaning |
|---|---|
| Proved reserves (developed / undeveloped / total, by product) | Oil and gas confirmed to be in the ground and profitably extractable |
| Standardized measure / PV-10 | The SEC's standardized present-value estimate of those reserves |
| Annual production volumes by product | How much was actually produced |
| Oil & gas revenue | What the production sold for |
| Capital expenditure | Spending on new drilling |

### Extraction layer — schema-constrained LLM, mandatory source span *(not started)*

| Field | Plain meaning |
|---|---|
| Average realized price per unit | What the company was actually paid per barrel or Mcf — before and after hedging |
| Production cost (LOE) per BOE | Cost to lift one barrel; realized price minus this is roughly the unit margin |
| Cash G&A per BOE | Corporate overhead allocated per barrel |
| Production guidance range | Management's own forecast — **sourced from 8-K EX-99.1, not the 10-K** |

### Derivation layer — pure Python, unit tested *(not started)*

Reserve life (R/P ratio), reserve replacement ratio, finding & development cost per BOE, unit margin per BOE.

*BOE — "barrel of oil equivalent" — is the industry's common unit for adding oil and natural gas volumes into a single figure.*

## Coverage, measured

Across all 91 cohort members, after an exhaustive sweep of every form each filer has submitted:

| Concept | Filers reporting it |
|---|---|
| Capital expenditure | 71 / 91 |
| Oil & gas revenue | 71 / 91 |
| Standardized measure | 47 / 91 |
| Total proved reserves | 43 / 91 |
| Proved developed reserves | 40 / 91 |
| Production volume | 37 / 91 |
| Proved undeveloped reserves | 37 / 91 |
| **Average realized price** | **8 / 91** |
| **Production cost per BOE** | **2 / 91** |

**The most important result here is a negative one.** Broadening ingestion from 10-K only to every form the filer submits barely moved the two commercially valuable fields. Realized price reaches 8 filers in 91; production cost per BOE reaches 2. This was previously a sampling finding; it now holds after a complete sweep, which means those fields are not missing because of how Basin fetches — they are genuinely untagged.

Both are mandated disclosures under SEC Regulation S-K Subpart 1200 and ASC 932. The data is unambiguously present in the documents; it is simply not machine-readable. **That gap — between "the SEC requires this disclosed" and "nobody tagged it" — is precisely the gap a terminal subscription currently charges to close,** and it is the extraction layer's entire mandate.

Three findings that shaped how the cohort is read:

- **Tagged is not the same as current.** Filers tag a concept and then stop. Matador's reserve concepts end at FY2012, EOG's at FY2021, Amplify's at FY2018. Counting "ever tagged" overstates usable coverage by about a third, so currency is scored separately and shown per row.
- **Some of the largest producers tag no reserve data at all.** ConocoPhillips's entire `srt` namespace is two tags; Occidental has none. This is absence, not inconsistency — company size is anti-correlated with XBRL completeness often enough that a cohort cannot be picked by market cap.
- **Ranking by coverage alone selects the wrong companies.** The filers with perfect coverage are royalty, minerals and partnership vehicles, which tag cleanly because their disclosures are simpler. The operator filter has to come before the coverage ranking, not after.

## Architecture

Basin is a **pipeline, not an agent.** "Alert me when guidance changes" cannot be answered at request time. The system maintains a panel dataset that rebuilds itself as filings arrive. A language model appears in exactly two places: as a schema-constrained extractor at ingest, and as an explainer at read time. Chat is one surface over the table, not the system itself.

| Layer | Mechanism | How it is evaluated | Status |
|---|---|---|---|
| **Watch** | EDGAR daily index / submissions polling → filing events | liveness | not started |
| **Facts** | XBRL `companyfacts` → typed fact rows | exact match | **done** |
| **Verification** | Every stored value located in the document it cites | % found, per concept | **done** |
| **Extraction** | Per-vertical KPI schema; LLM constrained to schema; mandatory source span | labeled golden set, per-field P/R | not started |
| **Derivation** | Pure Python — growth, ratios, unit normalization | unit tests | not started |
| **Change detection** | Diff facts, extractions and narrative sections against prior, with materiality thresholds | precision on alerts | not started |
| **Delivery** | Tables, Excel export, alerts, chat | LLM judge | panel only |

Two consequences worth stating explicitly:

- **The system of record is relational, not a vector store.** Facts are typed rows with provenance, append-only and versioned by accession number. Restatements and amendments must not silently overwrite the history that citations depend on. Vector search serves the narrative layer only.
- **False alerts are the failure mode that kills trust.** Change detection will be evaluated on precision, not recall. An alert that turns out to be a rounding artifact costs more than a missed one.

### How a filer reports: two independent axes

Both are measured from the filer's own data, never inferred from domicile — which is a weaker signal than it looks. Four foreign-domiciled filers report US GAAP like any domestic company, and `KGEI` is US-domiciled while filing a 40-F under IFRS.

**`reporting_taxonomy`** answers *can the Facts layer read this filer at all*, from its `companyfacts` payload. **66 report `us-gaap`, 19 report `ifrs-full`,** 6 are unknown.

This qualifies every empty cell. Reserve and production disclosure is a US requirement, so those concepts live in the SEC's `srt` namespace and have **no IFRS counterpart whatsoever**. A blank reserve column means "never tagged by anyone" for an IFRS filer and "this filer did not tag it" for a US GAAP one — different findings that look identical without the axis on the row. For an IFRS filer the Facts layer reaches revenue and capex; reserves are extraction work against the 20-F or 40-F text.

> There is a trap here worth naming. `ifrs-full` defines `OtherReserves` and `ReserveOfExchangeDifferencesOnTranslation`, tagged by 9 and 4 of these filers. Those are **equity** reserves — retained amounts on the balance sheet — with nothing to do with hydrocarbons. A name-matched alias would have populated a reserves column with shareholders' equity, in plausible units, looking entirely correct. They are deliberately absent from the registry.

**`disclosure_regime`** answers *do two numbers mean the same thing*, and follows the SEC **form**, not the accounting standard. **81 filers are Subpart 1200, 8 are NI 51-101.**

- **10-K and 20-F → Regulation S-K Subpart 1200.** Form 20-F Item 4.D applies the same oil & gas regime a domestic 10-K uses, so Shell, BP, Equinor and Petrobras report proved reserves on SEC definitions despite reporting IFRS financials. Checked against the filings: "proved reserves" appears 73, 112, 11 and 44 times in their current 20-Fs.
- **40-F → Canadian NI 51-101,** via the MJDS. Genuinely different definitions: reserves evaluated at **forecast prices** rather than the SEC's trailing 12-month average, a **2P (proved plus probable)** headline where the SEC's is proved alone, and **pre-tax values at several discount rates** instead of a single after-tax 10% standardized measure.

So Shell is IFRS *and* comparable; Cenovus is IFRS *and* not. Only the second is a comparability problem, and one field could not have said so. The panel badges both.

### Reading the filing a value is actually in

A 40-F is frequently a cover sheet. Cenovus's primary document is **15,395 characters** and carries no reserve disclosure at all; the NI 51-101 reserve statements are in the attached Annual Information Form.

| Filer | Reserve hits in the primary document | Richest document |
|---|---|---|
| BTE, CVE, GFR, OBE, SU, VET | **0** | EX-99.1 — 40 to 76 hits |
| KGEI | 2 | ex99-1.htm — 33 |
| CNQ | 90 | the 40-F itself |

Which exhibit holds them is not knowable from the index. Baytex files an AIF as EX-99.1 and an ASC 932 supplement as EX-99.4; Canadian Natural puts the discussion in the 40-F and files financial statements as EX-99.1. Most filers write "EX-99.1" in the description column rather than describing anything, so the declared type is no help either. **Size is the only usable signal** — certifications and consents are numerous, always a few kilobytes, and never carry a disclosure — so everything substantial is fetched and deciding what is in each is left to the parser. Fetching is the rate-limited step and happens once; parsing happens on every read.

## Comparability: the core claim and the core liability

Putting ninety companies in one table asserts that the rows are comparable. Frequently they are not.

**Design decision: definition mismatch is a first-class field on the cell, not a footnote.** A table that says "these four report on a different basis, and here is the difference" is trustworthy. A visually clean table that quietly mixes bases is the thing that loses an account.

The first live instances turned up in the Facts layer, before any language model was involved:

- **Products collide inside one concept.** XBRL dimensions reserves and realized price by product, but the `companyfacts` API flattens the dimension away. Matador's oil and gas prices arrived as two values on one concept, distinguishable only by unit. Product is therefore part of a cell's identity — oil and gas are separate rows, not competitors for one cell.

- **A filer's own unit label can be wrong.** Devon tags total proved reserves as `MMBoe` through FY2022 and `MMcfe` from FY2023, while the values run continuously (2182 → 1817 → 2155). A genuine BOE-to-cfe change would move the figure roughly sixfold; their *developed* reserves stay in `MMBoe` at comparable magnitude in the same filings.

- **The declared unit does not determine the magnitude.** Diamondback reports proved developed reserves as `2,521,028,000` tagged `MBoe` — base units under a presentational label, since their reserves are ~2.5 billion BOE. Devon reports `2,155` tagged `MMcfe`, scaled to match the label. Both are internally coherent; neither is inferable from `(value, unit)` alone.

- **Alias choice is a measurement, not a default.** Which tag a filer means by "total proved reserves" is decided per filer by testing an identity that must hold in every filing — developed + undeveloped = total — across every combination of tags that filer uses. **35 filers validate, 93 lack the concepts, and 2 *drifted*:** the identity held for years and then stopped, because the tag kept its name and changed its meaning. Recency is scored separately from frequency, since a panel showing FY2025 is not helped by agreement in FY2012.

- **A taxonomy migration silently truncates history.** Filers move the same tag name from `us-gaap:` to `srt:` mid-history. Reading only the first match dropped everything before the move — Continental's developed reserves began at 2016 instead of 2011.

- **A missing alias reads exactly like a missing disclosure.** The registry had `ProvedUndevelopedReserveBOE` but not `ProvedUndevelopedReserveBOE1`, which a sweep of 40 filers found in 14 of them against 3 for the form it did have. Coverage numbers measure the registry as much as the filers, which argues for checking them against raw payloads rather than trusting them.

- **The arithmetic does not always close.** Two identities have to hold inside one filer's own numbers: developed ≤ total, and developed + undeveloped = total. The `reserve_consistency` view surfaces every company-period that fails. The sum check is the one that earns its place, because it localises the fault: Antero's components sum to **17,261 MMcfe** against a total reading **17,261,000** — identical digits, a factor of a thousand apart.

Basin does not rewrite a filer's unit — inventing a corrected label is worse than reporting the filer's own. The `unit_discontinuity` view surfaces all **158** series whose declared unit changes, and the stored `value` is never touched: a resolved magnitude sits beside it in `fact_scale`, with the evidence that produced it.

**Resolving a magnitude takes two steps, and they are different in kind.** The scale the filing prints a figure at is *measured*, by finding the value in the document. That leaves two candidate readings — as tagged, and as printed — and choosing between them is *inference*. Verified scale alone cannot decide it: Diamondback and CNX both verify at a scale of 1,000, and the correct reading is the opposite one in each case.

What decides it is an economic identity. The standardized measure is a discounted present value of the same reserves, in dollars, so one divided by the other is a value per barrel — and that number has a range no real producer falls outside. Diamondback reads $10.20/BOE descaled against $0.01 as tagged; CNX reads $3.15/BOE as tagged against $3,146 descaled. The monetary side anchors it, because XBRL monetary facts are reliably tagged in dollars while volume unit labels are not.

**The unit family comes from the document too.** Scale resolution alone trusts the tagged unit, and that label is sometimes wrong in a way no scale arithmetic can see — Gulfport tags total proved reserves in `bbl` under a table headed `Total (Bcfe)`. Units are read from the filing at verification time, both inline and from column headers, and each becomes a candidate reading. Read as barrels Gulfport implies $0.80/BOE, which clears a wide sanity check and is still not a number a producer reports; read as Bcfe it implies $4.80. Readings inside the typical $1.50–$50 range win first, then readings the document states over the one the filer tagged.

Gulfport drops from 4.25 billion BOE to **708.8 million**, and Devon's `MMcfe` mislabel resolves to **2,428 MMBoe at $7.73/BOE**, settled by evidence from the document rather than by assumption. Every resolution records the ratio it turned on and the readings it rejected; a cell whose candidates are all implausible stays unresolved rather than guessed at. **2,601 cells are resolved.**

This is the sharpest form of the comparability problem so far, and it lands in the Facts layer — the one that was supposed to be the easy, exact one. XBRL removes the risk of a *fabricated* number; it does not deliver a *comparable* one.

## Verification: a citation is a claim until it is checked

**XBRL asserts a citation rather than proving one.** The accession attached to a fact comes from the SEC, not from having looked. Until the figure has been located in the document, the citation is a claim.

Every stored value is checked against the filing it cites. Verification fetches the filing's documents, flattens them to text, finds the value, and records the verbatim span, the page, the line, the `Item N.` heading and the scale the filing printed it at.

**9,502 facts checked. 9,300 located verbatim — 97%.**

| Status | Count | |
|---|---|---|
| found | 9,300 | 97% |
| not_found | 90 | 1% |
| unverifiable | 112 | 1% |

Every current cell in the store has been checked.

**62% of verified facts are stored at a different scale than the filing prints them,** and **75 filers use different scales for different concepts**, because reserve tables and financial statements are presented differently in the same document.

Two things this pass taught:

- **Location method shifts with filing age.** On 2023+ filings, 98% of figures were found in markup. On pre-2023 filings it is **54% markup / 46% text** — older EDGAR HTML carries far less structured tagging, so nearly half of historical values lean on the weaker method.
- **Verification must depend on the filing, not on an earlier download.** It originally searched only documents already in the corpus, which silently bounded it by another script's scope: a 40-F or 6-K whose exhibits were never fetched had only its cover sheet to search, and every figure in it recorded as not found. **186 revenue and capex facts failed this way**, 125 on those two forms. Exhibits are now read from the filing index and fetched on demand, which recovered 148 of 238 failures and eliminated the 6-K cases entirely.

The remaining 90 are a diffuse tail across eight forms and nine concepts, with no dominant cause.

## Running it

The SEC requires a declared `User-Agent`; the client refuses to make a request without one rather than sending a default. Cohort sync needs a Finviz Elite API token, read from the environment and never stored in the repository.

```bash
uv venv && uv pip install -e ".[dev]"
export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"
export FINVIZ_AUTH_TOKEN="..."          # Elite export API token

# Cohort: pull the producing industries, resolve tickers to CIKs, reconcile
python scripts/sync_cohorts.py --apply
python scripts/sync_tickers.py --apply      # canonical tickers from the SEC
python scripts/resolve_succession.py --apply  # follow changes of registrant
python scripts/sync_taxonomy.py --apply     # reporting taxonomy + disclosure regime
python scripts/check_producers.py --apply   # does this filer own reserves?

# Facts
python scripts/ingest_xbrl.py --cik 1090012 --forms all
python scripts/fetch_filings.py             # documents, including 40-F exhibits
python scripts/verify_facts.py --limit 9000 --since 2000-01-01
python scripts/resolve_scales.py            # canonical magnitudes in BOE / USD

pytest
```

Every script reports before it writes; `--apply` is always required to change the store.

### The dashboard

```bash
uv pip install -e ".[web]"
python -m uvicorn basin.web.app:app --port 8422
```

Five views over the store at `http://localhost:8422`:

- **Panel** — one metric, every company in a cohort. Defaults to each filer's own **latest reported** period, because 7 cohort members close their fiscal year outside December and a fixed period drops them entirely; every row states the period it came from and flags how far behind the panel it is. Rows are grouped by declared unit, ranked by each company's largest value with a company's rows kept together, and badged with IFRS and NI 51-101 where those apply. Each row shows which of the nine KPIs that filer reports; clicking one switches the metric. Clicking a company opens its **reported history** — small multiples, one series per product and unit, with a normalize toggle.
- **Trends** — one KPI over time across companies, on canonical magnitudes, annual periods only.
- **Coverage** — the companies × concepts matrix, distinguishing current from stale from never-tagged. The blank space is the extraction layer's mandate, drawn to scale.
- **Companies** — cohort members by how much data each actually has.
- **Data quality** — reserve arithmetic that does not close, unit discontinuities, and fallback tags. Nothing here is silently corrected.

The app opens the store read-only, and every query lives in `basin.store.queries`, so what the browser renders and what the tests assert on are the same code.

## Roadmap

### Done

- [x] **`xbrl_facts`** — rate-limited EDGAR client, concept registry with `srt:` / `us-gaap:` / `ifrs-full:` aliasing, typed fact rows, per-company coverage scoring currency as well as presence
- [x] **Fact store schema** — `(concept, value, unit, period, accession, form, extracted_by, source_span)`, append-only, with a `fact_current` view for reads. An LLM-sourced row without a source span is rejected by a `CHECK` constraint rather than by review.
- [x] **The cohort, defined and defended** — 91 producers from a real industry classification, with a producer test that excluded five non-producers on recorded evidence
- [x] **Identity** — ticker for presentation, CIK for keying, a partial unique index to enforce it, and an evidence-based resolver for changes of registrant
- [x] **Two reporting axes** — taxonomy (can we read it) and disclosure regime (does it mean the same thing), both measured
- [x] **A document corpus** — 4,269 documents including 8-K EX-99.1 earnings releases and 40-F exhibits, because the reserve disclosure is often not in the document the filing points at
- [x] **Document verification** — every current cell located in the filing it cites, with the matched span, page, line and printed scale. 97% of 9,502
- [x] **A comparable panel** — resolved magnitudes with the evidence and the rejected readings recorded; unresolved cells shown unranked rather than guessed at
- [x] **Page and line locators** — clicking any value opens the filing, page and line it came from
- [x] **Reported history per company** — every past disclosure of a metric, split by product and unit so a relabelling reads as a break rather than a change

### Next

- [ ] **Golden set** — hand-label the extraction fields × cohort × recent periods, starting with realized price and LOE per BOE, which XBRL reaches for 8 and 2 filers respectively
- [ ] **Extraction layer** — schema-constrained LLM against the 10-K, 20-F and 40-F text, with a mandatory verified source span, evaluated per-field against the golden set
- [ ] **Filing watcher** over EDGAR submissions, so new filings trigger ingest
- [ ] Chase the 90 remaining verification failures, and the 6 filers whose taxonomy is unknown

### Deferred

8-K item-code filtering (4.02 restatements, 5.02 executive departures, 1.01 material agreements) · Form 4 insider transactions · 10-K/A amendment awareness · segment-level extraction, so an Integrated filer's upstream segment can join the E&P comparison · EDGAR full-text search via `efts.sec.gov` *(endpoint shape unverified — confirm before relying on it)*

## Working principles

- **Nothing is described as working without being run.**
- **Citation spot-checks are part of "done."** A claimed source span must be verified to appear verbatim in the cited document before the value counts as correct.
- **Count before committing.** Filer populations and data availability are measured against live APIs, not estimated. Every figure in this document was produced that way, and several corrected earlier optimistic assumptions.
- **Absence of evidence is recorded as absence of evidence.** `unknown` and `unconfirmed` are first-class verdicts, distinct from a negative finding.

---

## Related

- [`EDGAR-INTELLIGENCE-HANDOFF.md`](EDGAR-INTELLIGENCE-HANDOFF.md) — the session handoff that established this direction, including the reasoning behind separating this from the FinAgent platform repository.

## Data sources

Filings and XBRL come from the SEC's public EDGAR system and its free APIs at `data.sec.gov`. No API key is required; SEC guidelines require a declared `User-Agent` and cap request rates at 10 per second.

Industry classification comes from the Finviz Elite screener export API, which requires a paid account token. It is used only to decide cohort membership — no financial data is taken from it.
