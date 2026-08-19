# Basin

**A consolidated E&P database of US oil & gas producers, built from SEC filings, with a citation behind every number.**

The operating data for exploration & production companies exists, publicly and for free, and it is unusable in the form it is published in. Reserves, production, realized prices, per-barrel costs and capex are scattered across hundreds of pages of prose, tables and inconsistently-tagged XBRL, in 91 separate filers' documents, restated and relabelled from year to year.

Basin consolidates that into one queryable panel — companies × metrics × periods — maintained as filings arrive. Every value carries the accession number, form, fiscal period, page, line and verbatim source text it came from, so any figure can be checked against the original document in one click.

It is built for people who need this data and cannot justify an enterprise terminal seat: smaller investment firms, lenders, consultants, corporate development teams, and accounting firms.

> **Status: Facts layer complete and verified, with a consolidated panel over it.** 91 companies across two cohorts, 19,729 facts drawn from 3,679 cited filings, and **every current cell checked against the document it cites — 98% located verbatim**. The panel is now the table the project is named for: every company against every KPI at once, not one metric at a time. The extraction, derivation, change-detection and delivery layers are not started. See [Roadmap](#roadmap).

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

- **A consolidated panel** — every producer, every metric, every period, in one table: a row per company and product, a column per KPI.
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

Membership is not a hand-written list. It is derived from **the SEC's own SIC classification**, which EDGAR publishes free in the submissions API and — critically — lets you enumerate in reverse: every filer under a code. Three codes hold companies that own hydrocarbon reserves:

| SIC | EDGAR's description | Cohort |
|---|---|---|
| `1311` | Crude Petroleum & Natural Gas | Oil & Gas E&P |
| `6792` | Oil Royalty Traders | Oil & Gas E&P |
| `2911` | Petroleum Refining | Oil & Gas Integrated |

Drilling and field services sell to producers; midstream gathers and fractionates third-party volumes under fee contracts, booking throughput rather than reserves. Neither has a reserve base, a lifting cost or a production volume — which is to say neither has the metrics this schema is built out of.

**A cohort is a KPI schema, not a label.** Comparison is legal within one and forbidden across, because putting an E&P and a pipeline in one reserves table asserts a comparability that does not exist. The panel enforces this rather than leaving it to the reader.

### SIC proposes; the filing disposes

A SIC code records what a registrant *registered as*, not what it does, and it is never revisited. On its own it is far too noisy to assign a cohort on: SIC 1311 sweeps in shells, midstream partnerships, refiners and — observed in the population — a biotechnology company. So the code is a proposal, closed by two things that are not the code.

**Thirteen filers carry an explicit override**, each one line of source with its reason ([`src/basin/cohorts.py`](src/basin/cohorts.py)). Seven are integrated majors EDGAR codes `1311` rather than `2911` — Shell, TotalEnergies, Eni, Petrobras, Ecopetrol, Cenovus — for no reason visible in the filings; left uncorrected they would join the E&P cohort and the panel would put an integrated filer's consolidated per-BOE costs beside a pure-play's. ConocoPhillips is the reverse: still coded `2911 Petroleum Refining`, which it has not been since it spun off Phillips 66 in 2012. A deviation from the SEC's classification is a decision, so it is written down where it can be argued with, rather than absorbed into a name-matching heuristic.

**Every other candidate must earn membership by disclosure.** A filer joins the cohort only once `scripts/check_producers.py` has recorded that its annual report was read and reserves were found — a company that lifts hydrocarbons has a reserve base and says so, either in tagged concepts or in prose. Candidates SIC proposes and no filing has confirmed are reported and held out, never admitted quietly.

Five filers sit in a producing code and produce nothing:

| Excluded | Why |
|---|---|
| `TGS` | Transportadora de Gas del Sur — an Argentine gas pipeline. **0 reserve-language hits in 1.27M characters** of its 20-F; tags only revenue and capex |
| `SLNG` | Stabilis Solutions — small-scale LNG distribution, not upstream. 0 hits |
| `VIVK` | Vivakor — oilfield waste remediation and crude transport. 0 hits |
| `NRT` | North European Oil Royalty Trust — passive royalty on German concessions. 4 hits, all risk-factor prose, against **30–90 for every other royalty trust in the cohort** |
| `CKX` | CKX Lands — a Louisiana land lessor. Its 10-K states the position outright: reserve information *"is not available. A schedule indicating such reserve quantities is, therefore, not presented."* |

SIC also settles a question name-matching could only guess at. `6792 Oil Royalty Traders` is EDGAR's own code for royalty and minerals vehicles, so it decides `is_operator` outright: those filers hold an interest in production someone else lifts, and a blank lifting cost against them is the business model rather than a coverage gap. Texas Pacific Land is the case that proves it — nothing in its name says royalty, and the old substring test called it an operator.

The check runs over the whole cohort and reports three verdicts. `unknown` — nothing was available to test — is deliberately distinct from `non-producer`, which means the filing was read and holds no reserves. Current standing: **89 producers, 5 non-producers (all excluded), 3 untested shells.**

Royalty and minerals vehicles like Dorchester, Black Stone and Texas Pacific Land **pass**, because they genuinely own reserves even though they operate nothing. That is a different distinction from `is_operator`, and both are kept.

Excluding a company clears its membership and nothing else. Its facts, filings and citations stay exactly as they were — the store is append-only, and being out of scope is not a reason to destroy history that other rows cite.

## Identity: ticker and CIK are not interchangeable

Basin **presents by ticker and keys by CIK**, because the two identifiers fail in opposite directions.

A CIK is assigned once and never reused. A ticker is released when a company delists and can later be reassigned to an unrelated filer — so keying facts on one would let two companies' histories merge silently, which is the failure an append-only store exists to prevent. Ticker is the identity in the panel, the URL and the export; it is never the thing a fact points at. A partial unique index makes a collision impossible rather than unlikely.

`NULL` is meaningful: it means the filer has no current listing, not that the ticker is unknown. **14 companies in the store are in that state** — taken private, acquired, or delisted — and they still file, still carry facts, and still have to be citable. Continental Resources went private in 2022 and keeps filing 10-Ks because of its public debt.

### Scope: traded US securities

Basin covers **traded US securities**, and cohort membership comes from a screener, so a producer with no live listing is excluded automatically. That is a deliberate scope decision. What is not acceptable is the exclusion being invisible, so every filer in the store carries a `listing_status` — `listed` (120), `private-filer` (8), `deregistered` (6) or `superseded` (1) — with the date it last filed and a note saying how the verdict was reached.

A filer with no listing is one of two very different things, and the difference is measurable. **Form 15** certifies termination of registration — the filer telling the SEC it intends to stop reporting — but saying so is not doing so. Read Form 15 against what was filed afterwards and the group splits cleanly:

**Private filers — no listing, still filing periodic reports. This is the real gap:**

| Company | KPIs | Last 10-K/10-Q | |
|---|---|---|---|
| Energy 11, L.P. | 7 | 2026-08-12 | non-traded partnership, never listed |
| Energy Resources 12, L.P. | 6 | 2026-08-12 | non-traded partnership, never listed |
| Everflow Eastern Partners LP | 4 | 2026-08-11 | non-traded partnership, never listed |
| Continental Resources | 7 | 2026-07-31 | **filed Form 15 in 2023 and kept reporting** — public debt keeps the obligation alive |

**Acquired or wound up — filed Form 15 and stopped. Not a gap; the filer is gone:**

| Company | Deregistered | Last 10-K/10-Q |
|---|---|---|
| Coterra Energy | 2026-05-19 | 2026-05-06 |
| Civitas Resources | 2026-02-10 | 2025-11-06 |
| Berry Corp | 2026-01-09 | 2025-11-05 |
| Vital Energy | 2025-12-29 | 2025-11-03 |
| Sitio Royalties | 2025-09-02 | 2025-08-04 |
| PHX Minerals | 2025-07-03 | 2025-05-08 |

Conflating the two overstated the gap at nine when it is four. The facts of all ten are already in the store — they are simply not in a cohort, and so never in a peer table.

The four are also fewer than the eight filers marked `private-filer`, and for a second reason: a filer only counts as a producer being missed if it holds a reserve or production concept. Revenue and capex alone describe a company that spends money and sells something, which is how Rivulet Entertainment survived the old SIC-1311 sweep. The query tests for a reserve base rather than for any fact at all.

The **Data quality** view lists the four, because a gap in the dataset belongs beside the other things the store knows are wrong with it rather than buried in a column nobody queries. Closing it means a second membership path for filers with no listing, which is not built and not currently planned.

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

The number that matters is not how much XBRL carries. It is the distance between what XBRL carries and what is **publicly locatable in the filings** — because that distance is the product.

Measured across the 89 producer-verified cohort members. "Locatable" means the full-text index finds the disclosure's table in an annual report already in the corpus, on a filer the SEC requires to publish it:

| Panel column | In XBRL | Locatable in the filings | The gap |
|---|---|---|---|
| Capital expenditure | 71 / 89 | — | — |
| Oil & gas revenue | 71 / 89 | — | — |
| Proved developed reserves | 50 / 89 | **86 / 89** | +37 |
| Total proved reserves | 49 / 89 | **86 / 89** | +38 |
| Proved undeveloped reserves | 49 / 89 | **86 / 89** | +37 |
| Standardized measure | 47 / 89 | **87 / 89** | +40 |
| Production volume | 37 / 89 | **68 / 89** | +34 |
| **Average realized price** | **8 / 89** | **68 / 89** | **+60** |
| **Production cost per BOE** | **2 / 89** | **68 / 89** | **+68** |

**The most important result here is a negative one, and it has a positive on the other side of it.** Broadening ingestion from 10-K only to every form each filer submits barely moved the two commercially valuable fields: realized price reaches 8 filers in 89, production cost per BOE reaches 2. Those fields are not missing because of how Basin fetches. They are genuinely untagged.

And they are genuinely *there*. Both are mandated by Regulation S-K Subpart 1200, both are printed in every one of those filings, and the full-text index finds the table carrying them in 68 of 89. The same holds for reserves: XBRL reaches half the cohort, the reserve table is locatable in 86 of 89.

**That distance — between "the SEC requires this disclosed" and "nobody tagged it" — is precisely what a terminal subscription charges to close.** It is the extraction layer's entire mandate, and it is measurable rather than asserted, which is what makes it a target instead of a hope.

Two mechanisms close it, and the second is what makes the first affordable:

- **Read the table, not the tag.** A figure read from a reserve or production table carries no scale to resolve — the figure is the figure as printed, and its unit is its column header. This is structurally safer than XBRL for exactly the errors XBRL makes: Range tags oil reserves `21,290 MMBbls` in its own markup while the same column of the same table reads `(MBbls)`, a thousand-fold error the table path cannot have.
- **Locate before parsing.** A 10-K holds a few hundred tables and the caller has to know which document to open at all. [`document_search`](src/basin/store/schema.sql), an FTS5 index over 13.2 million lines of the corpus, answers "which documents use this disclosure's language, and where do its rows cluster" in one query. On a 60-document sample it flagged every document the reserve extractor could read and ruled out 27 of 60 outright.

What is locatable is not yet what is extracted; the current shortfall is measured and enumerated in [`docs/panel-gaps.md`](docs/panel-gaps.md).

Three findings that shaped how the cohort is read:

- **Tagged is not the same as current.** Filers tag a concept and then stop. Matador's reserve concepts end at FY2012, EOG's at FY2021, Amplify's at FY2018. Counting "ever tagged" overstates usable coverage by about a third, so currency is scored separately and shown per row.
- **Some of the largest producers tag no reserve data at all.** ConocoPhillips's entire `srt` namespace is two tags; Occidental has none. This is absence, not inconsistency — company size is anti-correlated with XBRL completeness often enough that a cohort cannot be picked by market cap.
- **Ranking by coverage alone selects the wrong companies.** The filers with perfect coverage are royalty, minerals and partnership vehicles, which tag cleanly because their disclosures are simpler. The operator filter has to come before the coverage ranking, not after.

## Architecture

Basin is a **pipeline, not an agent.** "Alert me when guidance changes" cannot be answered at request time. The system maintains a panel dataset that rebuilds itself as filings arrive. A language model appears in exactly two places: as a schema-constrained extractor at ingest, and as an explainer at read time. Chat is one surface over the table, not the system itself.

| Layer | Mechanism | How it is evaluated | Status |
|---|---|---|---|
| **Watch** | EDGAR daily index / submissions polling → filing events | liveness | not started |
| **Facts** | XBRL `companyfacts`, plus inline XBRL read from the filings for the dimensions the API drops → typed fact rows | exact match | **done** |
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

- **Products collide inside one concept.** XBRL dimensions reserves and realized price by product, but the `companyfacts` API flattens the dimension away. Matador's oil and gas prices arrived as two values on one concept, distinguishable only by unit. Product is therefore part of a cell's identity — oil and gas are separate rows, not competitors for one cell. Recovering the product from the unit only works where the unit is unambiguous, so the dimension is read where it survives: the filings themselves are inline XBRL and keep it. **5,408 of the 19,729 facts come from that second path** (`extracted_by = xbrl:inline`) against 14,321 from `companyfacts`, and only *dimensioned* facts are written from it — the undimensioned roll-up is already in the store, and re-inserting it would put two rows on one cell.

- **A filer's own unit label can be wrong.** Devon tags total proved reserves as `MMBoe` through FY2022 and `MMcfe` from FY2023, while the values run continuously (2182 → 1817 → 2155). A genuine BOE-to-cfe change would move the figure roughly sixfold; their *developed* reserves stay in `MMBoe` at comparable magnitude in the same filings.

- **The declared unit does not determine the magnitude.** Diamondback reports proved developed reserves as `2,521,028,000` tagged `MBoe` — base units under a presentational label, since their reserves are ~2.5 billion BOE. Devon reports `2,155` tagged `MMcfe`, scaled to match the label. Both are internally coherent; neither is inferable from `(value, unit)` alone.

- **Alias choice is a measurement, not a default.** Which tag a filer means by "total proved reserves" is decided per filer by testing an identity that must hold in every filing — developed + undeveloped = total — across every combination of tags that filer uses. **35 filers validate, 93 lack the concepts, and 2 *drifted*:** the identity held for years and then stopped, because the tag kept its name and changed its meaning. Recency is scored separately from frequency, since a panel showing FY2025 is not helped by agreement in FY2012.

- **A taxonomy migration silently truncates history.** Filers move the same tag name from `us-gaap:` to `srt:` mid-history. Reading only the first match dropped everything before the move — Continental's developed reserves began at 2016 instead of 2011.

- **A missing alias reads exactly like a missing disclosure.** The registry had `ProvedUndevelopedReserveBOE` but not `ProvedUndevelopedReserveBOE1`, which a sweep of 40 filers found in 14 of them against 3 for the form it did have. Coverage numbers measure the registry as much as the filers, which argues for checking them against raw payloads rather than trusting them.

- **The arithmetic does not always close.** Two identities have to hold inside one filer's own numbers: developed ≤ total, and developed + undeveloped = total. The `reserve_consistency` view surfaces every company-period that fails. The sum check is the one that earns its place, because it localises the fault: Antero's components sum to **17,261 MMcfe** against a total reading **17,261,000** — identical digits, a factor of a thousand apart.

Basin does not rewrite a filer's unit — inventing a corrected label is worse than reporting the filer's own. The `unit_discontinuity` view surfaces all **158** series whose declared unit changes, and the stored `value` is never touched: a resolved magnitude sits beside it in `fact_scale`, with the evidence that produced it.

**Resolving a magnitude takes two steps, and they are different in kind.** The scale the filing prints a figure at is *measured*, by finding the value in the document. That leaves two candidate readings — as tagged, and as printed — and choosing between them is *inference*. Verified scale alone cannot decide it: Diamondback and CNX both verify at a scale of 1,000, and the correct reading is the opposite one in each case.

What decides it is an economic identity. The standardized measure is a discounted present value of the same reserves, in dollars, so one divided by the other is a value per barrel — and that number has a range no real producer falls outside. Diamondback reads $10.20/BOE descaled against $0.01 as tagged; CNX reads $3.15/BOE as tagged against $3,146 descaled. The monetary side anchors it, because XBRL monetary facts are reliably tagged in dollars while volume unit labels are not.

**The unit family comes from the document too.** Scale resolution alone trusts the tagged unit, and that label is sometimes wrong in a way no scale arithmetic can see — Gulfport tags total proved reserves in `bbl` under a table headed `Total (Bcfe)`. Units are read from the filing at verification time, both inline and from column headers, and each becomes a candidate reading. Read as barrels Gulfport implies $0.80/BOE, which clears a wide sanity check and is still not a number a producer reports; read as Bcfe it implies $4.80.

Candidates are ranked in three steps: readings inside the typical **$1.50–$50** band win first; among those, the one closest to the middle of the band on a log scale, because candidates differ by orders of magnitude rather than percentages; and only then the reading the document states over the one the filer tagged. **The document used to outrank closeness, and that was wrong.** A table header is evidence about the unit, not proof of it — it is read from whichever table the value was located in, and a filing has many tables. W&T tags gas reserves as `423,300,000,000 ft3` — 423.3 Bcf, correct — under a header reading `MMBoe`. Both readings clear the band, but the tagged one implies $9.23/BOE and the header one $1.54, at the very edge. Ranking the document first took the edge reading and multiplied the reserve base sixfold. Nothing that needs the header loses by the reordering: Gulfport has only one reading inside the typical band at all, so it is settled before the tie-break is consulted.

**Scale and unit are shared to different extents, and conflating them was a bug.** One decision per company and period governs the reserve table, because "in thousands" at the top of a page applies to every line under it — so the resolved *divisor* carries to every reserve row in that period. The *unit* does not: a filer reporting by product prints oil in MBbls, gas in MMcf and the total in MBoe in the same table. Forcing the anchor's unit onto every row read W&T's 423.3 Bcf of gas as 423.3 billion million BOE — 4.2e17, more than world reserves — and made 93.5 billion BOE out of Viper's 93.5 million barrels of oil. The resolved unit is now applied only where the row declared the same unit as the anchor; a row declaring something else is read as tagged. Every reserve row then takes the value-per-barrel test on its own, not just the anchor, because a filer can mislabel a product line while the line the anchor came from is right — Range's 2019 oil is `74,532` tagged MMBbls in a table printed in MBbls, 74.5 billion barrels instead of 74.5 million, a fault no shared scale can see. A product line is additionally required not to exceed the reserve base it is part of, which is an identity rather than a band and so catches what a plausibility range cannot.

**Rejection has to be able to undo.** A resolver that merely declines to write leaves whatever an earlier, worse run wrote — which is how W&T's 4.2e17 survived the plausibility guard added to catch exactly it. Rejecting a reading now clears any magnitude already stored for that fact.

Gulfport drops from 4.25 billion BOE to **708.8 million**, and Devon's `MMcfe` mislabel resolves to **2,428 MMBoe at $7.73/BOE**, settled by evidence from the document rather than by assumption. Every resolution records the ratio it turned on and the readings it rejected; a cell whose candidates are all implausible stays unresolved rather than guessed at. **1,819 cells are resolved** — down from 2,601 before these fixes, because several hundred were resolved wrongly and are now honestly blank. Readings above 1e11 BOE fall from 63 to none, and the largest magnitude in the store from 4.3e17 to 4.6e9, which is a reserve base a real producer holds. *(Measured against the re-verified store; the resolver has been revised again since, so this figure will move on the next run.)*

This is the sharpest form of the comparability problem so far, and it lands in the Facts layer — the one that was supposed to be the easy, exact one. XBRL removes the risk of a *fabricated* number; it does not deliver a *comparable* one.

## Verification: a citation is a claim until it is checked

**XBRL asserts a citation rather than proving one.** The accession attached to a fact comes from the SEC, not from having looked. Until the figure has been located in the document, the citation is a claim.

Every stored value is checked against the filing it cites. Verification fetches the filing's documents, flattens them to text, finds the value, and records the verbatim span, the page, the line, the `Item N.` heading and the scale the filing printed it at.

**9,502 facts checked. 9,304 located verbatim — 98%.**

| Status | Count | |
|---|---|---|
| found | 9,304 | 98% |
| not_found | 90 | 1% |
| unverifiable | 108 | 1% |

Every current cell in the store has been checked.

**64% of verified facts are stored at a different scale than the filing prints them,** and **76 filers use different scales for different concepts**, because reserve tables and financial statements are presented differently in the same document.

Three things this pass taught:

- **A match needs the filing's own precision, not a flat tolerance.** Accepting any tagged figure within 0.5% is wide enough, at reserve-table magnitudes, to match a different number entirely. Diamondback's proved reserves are 3,617,856 MBoe; the capitalized-costs table carries 3,613 at scale 6, which is 0.134% away, so verification matched an unrelated figure and recorded a printed scale taken from it. The resolver was then handed a scale from the wrong number, found two readings implying $0.01 and $10,202 per BOE, and correctly rejected both — the cell was lost to a verification fault, not a resolution one. The only difference a genuine match may carry is the rounding the filing applied when printing, so the allowance is now half of the last printed place: Diamondback's false match is 4,856,000 away against an allowance of 500,000. The whole store has been re-verified under this rule, and the counts above are that pass. It recovered Diamondback's FY2025 reserves, which now resolve at **$10.20/BOE**.
- **Tightening a match made verification find *more*, not less.** The expectation was that a stricter rule would push some `found` rows into `not_found`. Instead `found` rose slightly and `unverifiable` fell, because a loose match does not only accept a wrong figure — it consumes the fact, so the right figure elsewhere in the document is never reached. Precision and recall moved the same way here.
- **Location method shifts with filing age, and further than it looked.** On 2023+ filings, **99% of figures are found in markup**. On pre-2023 filings it is **27% markup / 73% text** — the loose tolerance had been flattering old filings by accepting weak markup matches, and once the allowance comes from printed precision, most historical values fall back to the text method. Nearly three-quarters of pre-2023 values lean on the weaker method, which is a worse position than the previous 54/46 suggested.
- **Verification must depend on the filing, not on an earlier download.** It originally searched only documents already in the corpus, which silently bounded it by another script's scope: a 40-F or 6-K whose exhibits were never fetched had only its cover sheet to search, and every figure in it recorded as not found. **186 revenue and capex facts failed this way**, 125 on those two forms. Exhibits are now read from the filing index and fetched on demand, which recovered 148 of 238 failures and eliminated the 6-K cases entirely.

The remaining 90 are a diffuse tail across eight forms and nine concepts, with no dominant cause.

## Running it

The SEC requires a declared `User-Agent`; the client refuses to make a request without one rather than sending a default. Nothing else needs credentials — every source is public and unauthenticated.

```bash
uv venv && uv pip install -e ".[dev]"
export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"

# Cohort: enumerate the producing SIC codes, reconcile membership
python scripts/sync_cohorts.py                # report first: it holds out
python scripts/check_producers.py --apply     # adjudicate held candidates
python scripts/sync_cohorts.py --apply
python scripts/sync_tickers.py --apply      # canonical tickers from the SEC
python scripts/resolve_succession.py --apply  # follow changes of registrant
python scripts/sync_taxonomy.py --apply     # reporting taxonomy + disclosure regime
python scripts/check_producers.py --apply   # does this filer own reserves?
python scripts/note_untraded.py --apply     # listed / private-filer / deregistered

# Facts
python scripts/ingest_xbrl.py --cik 1090012 --forms all
python scripts/validate_aliases.py          # which tag this filer means, per concept

# Documents, then everything that reads them
python scripts/fetch_filings.py             # documents, including 40-F exhibits
python scripts/index_documents.py           # flatten the corpus to pages and lines
python scripts/ingest_product_volumes.py    # the oil/gas/NGL split, from inline XBRL
python scripts/verify_facts.py --limit 9000 --since 2000-01-01
python scripts/resolve_scales.py            # canonical magnitudes in BOE / USD

pytest
```

**`--apply` guards revision, not writing.** The six scripts that carry it — `sync_cohorts`, `sync_tickers`, `resolve_succession`, `sync_taxonomy`, `check_producers`, `note_untraded` — all *change* what the store already says about a company, so they report the change first and require the flag to make it. The ingest, verification and resolution scripts append or recompute rather than revise, and write on run. Of those, `index_documents`, `ingest_product_volumes`, `validate_aliases` and `resolve_scales` fetch nothing at all — their input is the corpus and cache already on disk, so they are re-run whenever the parser or the resolver improves.

Two ordering constraints are real rather than stylistic, and both run through the corpus on disk rather than through the database. `fetch_filings.py` has to precede `ingest_product_volumes.py`, which reads inline XBRL out of the stored filings and skips any accession it does not find. And `verify_facts.py` has to precede `resolve_scales.py`, which decides magnitudes from the printed scale and the header units that verification recorded.

`index_documents.py` is deliberately outside that chain. Verification reads the corpus files directly and fetches what is missing on demand, so it does not wait on the index; the `document` / `document_line` tables and the full-text index exist ahead of the extraction layer, which is what will read them.

### The dashboard

```bash
uv pip install -e ".[web]"
python -m uvicorn basin.web.app:app --port 8422
```

Five views over the store at `http://localhost:8422`:

- **Panel** — the consolidated table itself: **one row per company and product, one column per KPI.** There is no metric picker, because a database whose objective is consolidation should not make reading a filer's reserves against its production and its capex take three selections and three screens. Rows are keyed by `(company, product)` rather than by company, since a filer legitimately holds several values for one concept and collapsing them would either drop data or invent a total the filer never reported. Defaults to each filer's own **latest reported** period, because 7 cohort members close their fiscal year outside December and a fixed period drops them entirely. Units live in the cells, not the headers — all nine columns span more than one declared unit, and proved reserves alone arrives in fourteen — so **a column is only sortable where its values share a scale**: with Normalize on, from the resolved magnitude, and as filed only where every filer chose the same unit. The header says which columns can be ranked and which cannot, rather than sorting a lie on request. An absent cell and a cell whose magnitude could not be resolved render differently, because they are different findings. Rows are badged with IFRS and NI 51-101 where those apply; clicking a value opens the filing, page and line it came from; clicking a company opens its **reported history** — small multiples, one series per product and unit, with its own metric selector.
- **Trends** — one KPI over time across companies, on canonical magnitudes, annual periods only.
- **Coverage** — the companies × concepts matrix, distinguishing current from stale from never-tagged. The blank space is the extraction layer's mandate, drawn to scale — which is exactly why this view is cohort-scoped. Drawn over every company the store ever ingested it disagreed with the panel by 44 companies, and the blanks belonging to SIC-1311 residue and to filers since acquired are not work to be done. The store-wide counts live here too, above the grid, because this is the view about what the dataset does and does not hold; they take the selected cohort, and the caption says which population is on screen.
- **Companies** — cohort members by how much data each actually has.
- **Data quality** — reserve arithmetic that does not close, unit discontinuities, fallback tags, and the four producers this scope cannot reach. Nothing here is silently corrected.

Cohort is **one selection shared across views**, not a control per view. Panel and Coverage answering "which companies" differently is the discrepancy the filter exists to remove, and two independent pickers would reintroduce it a click later.

The app opens the store read-only, and every query lives in `basin.store.queries`, so what the browser renders and what the tests assert on are the same code.

## Roadmap

### Done

- [x] **`xbrl_facts`** — rate-limited EDGAR client, concept registry with `srt:` / `us-gaap:` / `ifrs-full:` aliasing, typed fact rows, per-company coverage scoring currency as well as presence
- [x] **Fact store schema** — `(concept, value, unit, period, accession, form, extracted_by, source_span)`, append-only, with a `fact_current` view for reads. An LLM-sourced row without a source span is rejected by a `CHECK` constraint rather than by review.
- [x] **The cohort, defined and defended** — 91 producers from a real industry classification, with a producer test that excluded five non-producers on recorded evidence
- [x] **Identity** — ticker for presentation, CIK for keying, a partial unique index to enforce it, and an evidence-based resolver for changes of registrant
- [x] **Two reporting axes** — taxonomy (can we read it) and disclosure regime (does it mean the same thing), both measured
- [x] **A document corpus** — **4,269 documents across 3,559 of the 3,679 cited filings**, 1,267 primary and 3,002 exhibits, including 8-K EX-99.1 earnings releases and 40-F exhibits, because the reserve disclosure is often not in the document the filing points at. All of them are parsed into `document` / `document_line` and the full-text index: **13,245,443 lines**, each carrying the page and line coordinates a citation is read by. Indexing takes the corpus on disk as its input and fetches nothing, so it re-runs whenever the parser improves.
- [x] **Document verification** — every current cell located in the filing it cites, with the matched span, page, line and printed scale. 98% of 9,502
- [x] **The product split, from the filings** — 5,408 dimensioned facts read from inline XBRL, recovering the oil / gas / NGL axis the `companyfacts` API drops
- [x] **A comparable panel** — resolved magnitudes with the evidence and the rejected readings recorded; unresolved cells shown unranked rather than guessed at, and a rejection that clears what an earlier run wrote
- [x] **The consolidated table** — one row per company and product, one column per KPI, sortable only on columns whose values share a scale
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

Every input is a public SEC source, and there is no other. Filings, XBRL and the industry classification all come from EDGAR and its free APIs at `data.sec.gov` and `www.sec.gov`. No API key is required and no account is involved; SEC guidelines require a declared `User-Agent` and cap request rates at 10 per second, both enforced in [`src/basin/edgar/client.py`](src/basin/edgar/client.py) rather than at call sites.

This is deliberate. Basin previously took its industry classification from the Finviz Elite screener export, which classifies better than SIC but is a licensed feed: its terms do not contemplate redistributing the classification inside a product, and it was the single non-public dependency in an otherwise entirely public pipeline. Moving to SIC cost some precision and bought a supply chain with nothing in it that cannot be re-derived by anyone, from sources that are free to build on. See [`docs/commercial-compliance.md`](docs/commercial-compliance.md).
