# EDGAR Intelligence Product — Session Handoff

**Purpose of this doc:** hand off a *new product direction* that came out of a session in the FinAgent repo. This is **not** a FinAgent feature plan. The conclusion of that session was that this should be **a separate repo and a separate product**; FinAgent stays frozen as the platform reference piece.

Read `HANDOFF.md` in this repo for FinAgent context. Read this for the new thing.

**Status: nothing has been built yet.** No code was written. This is a direction, a set of architectural decisions, and a list of concrete findings about the existing FinAgent code that informed them.

---

## How this started

The user shared `2402.18485v3.pdf` — *"A Multimodal Foundation Agent for Financial Trading: Tool-Augmented, Diversified, and Generalist"* (Zhang et al., KDD '24, NTU/Skywork) — and asked how it could enhance FinAgent.

**Name collision worth knowing:** the paper's system is also called FinAgent. Unrelated project. It will come up if the user ever writes this up publicly, and it's one reason to name the new product something else.

What was concluded about the paper:

- **Doesn't transfer:** the RL/MDP formulation, buy/sell/hold action space, financial performance metrics (ARR/Sharpe/MDD), multimodal Kline-chart vision inputs. Their agent takes positions; the user explicitly does *not* want a trading agent.
- **Transfers, in principle:** diversified retrieval (§4.1 — separate query-text field at index time, M typed queries at query time, union of top-K each); dual-level reflection (§4.3) as a third learning mechanism next to SFT and the prompt optimizer; their cumulative ablation methodology (Table 5), which FinAgent's README lacks — it proves components exist but never says what each is worth.
- **Their most useful result is a negative one** (§7.2): auxiliary tools improved stock performance but dropped crypto by >20%, because equity-tuned tools were applied indiscriminately. Relevant to FinAgent's MoE gating — gating can fire *correctly* and still route to an expert that makes a query class worse. Aggregate pass rate hides this.

The paper then became mostly beside the point once the user reframed the goal (below). Retrieval refinements matter less than widening what the system can reach.

---

## The user's reframe (this is the actual direction)

Not "ask Apple's filings anything." Instead: **an industry-specific intelligence system built on EDGAR**, serving one narrow workflow better than enterprise terminals do, sold to buyers who can't justify enterprise seats — smaller investment firms, lenders, consultants, corp-dev teams, accounting firms.

The user's own framing example: *"Monitor every publicly traded copper producer and alert me when production guidance, cash costs, reserves, capex, or mine-development timelines change — then update my comparison table."*

Product wedges the user identified:
- "What changed?" reports comparing newest 10-Q/10-K against prior filings
- Extraction of industry-specific KPIs buried outside the standardized statements
- Covenant / debt / liquidity / dilution / insider-sale / risk-factor monitoring
- Alerts when management changes language, reporting definitions, segments, or accounting policies
- Peer benchmarking across 20–100 companies
- Excel-ready output with direct citations to every source passage

**The user's core technical insight, which is correct and should be preserved:** don't use RAG for everything. XBRL/Company Facts for standardized numbers → deterministic code for growth rates and ratios → document parsing and retrieval for narrative → LLM only to explain changes and organize evidence → exact filing, section, date, period, and source text behind every answer.

Sharpest version of that principle: **retrieval is a recall mechanism, not a precision mechanism.** Anything destined for a spreadsheet cell needs precision, so it must come from a typed extraction carrying a verified source span — a span assertable as appearing verbatim in the cited document. Similarity search finds the paragraph; it never populates the cell.

---

## Architectural conclusions

### 1. The vertical constraint is load-bearing, not just go-to-market

Open-ended KPI extraction has no ground truth → can't be evaluated → can't be trusted or improved. Fixing the industry and the KPI set turns it into a closed-set problem: N known companies × K known fields × periods. That makes a hand-labeled golden set possible, which makes per-field precision/recall possible, which makes CI regression possible. The eval harness produces the product's core asset.

### 2. It's a pipeline, not an agent

FinAgent is entirely request-time. "Alert me when guidance changes" cannot work that way. The system is a **self-maintaining panel dataset** (companies × KPIs × periods), rebuilt as filings arrive. The LLM appears in exactly two places: constrained extractor at ingest, explainer at read time. Chat is one surface over the table, not the system.

Consequence: **Chroma cannot be the system of record.** Primary store is relational, holding typed facts with provenance, append-only and versioned by accession — otherwise restatements and 10-K/As silently overwrite the history the citations depend on. Vector search demotes to serving the narrative layer only.

### 3. Layers, and what each is allowed to use

| Layer | Mechanism | How it's evaluated |
|---|---|---|
| Watch | EDGAR daily index / submissions polling → filing events | liveness |
| Facts | XBRL `companyfacts` → typed fact rows | exact match |
| Extraction | Per-vertical KPI schema; LLM constrained to schema; **mandatory source span** | labeled golden set, per-field P/R |
| Derivation | Pure Python — growth, ratios, unit normalization | unit tests |
| Change detection | Diff facts + extractions + narrative sections vs prior, with materiality thresholds | precision on alerts (false alerts kill trust) |
| Delivery | Tables, Excel export, alerts, chat | LLM judge |

### 4. Comparability is the core claim *and* the core liability

Putting 40 companies in one table asserts the rows are comparable. Often they aren't (C1 cash cost vs. AISC; one REIT's same-store pool definition vs. another's). Design decision: **surface definition mismatch as a first-class field on the cell.** "These four report on a different basis, here's the difference" is trustworthy; a clean table that quietly mixes bases is what loses an account.

---

## Vertical choice — OPEN DECISION, blocks the KPI schema

The user has not picked one. This determines the KPI schema, which everything else is built around. **Ask before proceeding.**

**Recommendation on the table: REITs**, over the user's copper example. Reasoning:

- **Copper has an EDGAR coverage problem.** Freeport and Southern Copper file 10-Ks, but much of the sector is Canadian — 40-F under MJDS (often IFRS, weaker XBRL tagging) or not SEC-registered at all and filing only on SEDAR. "Every publicly traded copper producer" may be a handful of reachable filers plus an unreachable tail. *Count the actual filer population before committing to any vertical.*
- **REITs fit the thesis almost exactly:** all domestic and on EDGAR, population large enough for real peer benchmarking, and the KPIs that matter (FFO, AFFO, same-store NOI, occupancy, lease expiry schedules, debt maturity ladders) are non-GAAP, sit outside the standardized statements, and are defined differently company to company — which is precisely the pain being sold into.
- Regional banks and BDCs have similar shape and are reasonable alternates.

**Also true of any vertical: guidance mostly isn't in the 10-K.** Production/operational guidance, cost outlook, and capex updates land in earnings releases — 8-K exhibit EX-99.1. Exhibit ingestion is a hard prerequisite, not a nice-to-have.

---

## Findings about the current FinAgent EDGAR code

These were read carefully this session. They inform what to copy and what to fix.

- `src/finagent/tools/edgar.py` — metadata only (form, date, URL) for one ticker/form type. Has a working `_ticker_to_cik()` that the XBRL work needs.
- `src/finagent/tools/filing_search.py` — single embedding query, hard-filtered to one ticker + one form_type.
- `src/finagent/rag/ingest.py` — indexes the **most recent filing only** (`limit=1`), and fetches **`primaryDocument` only** — no exhibits. Text extraction is `soup.get_text(separator="\n")`, which **destroys financial tables**, flattening them into columns of orphaned numbers.
- `src/finagent/rag/chunking.py` — section-aware "Item N." splitting with sliding-window fallback. Genuinely good; worth carrying over.
- ⚠️ **Latent bug relevant to any multi-period work:** the Chroma `where` clause filters on ticker + form_type only, with no period filter. The moment two 10-Ks are indexed, `filing_search` silently blends both years into one result set. Currently masked because only the latest filing is indexed.

---

## Decision: separate repo

Agreed and settled this session.

- **Audience is the deciding argument.** FinAgent's value is breadth of a coherent reference harness, read by a platform/ML team. The product's value is depth in one vertical plus citations that survive spot-checking, read by an analyst deciding whether to trust a number. Merging makes both worse: the platform stops being a clean demo, the product drags Terraform/k8s/Bedrock/MoE/W&B that no customer cares about.
- **Reuse is smaller than it feels:** `edgar.py` (~52 lines) plus `rag/` (chunking + ingest + store, ~225 lines). Under 300 lines — well below where a shared `finagent-core` package pays for itself. **Copy it; don't couple the repos.** Everything else that transfers is patterns, not code (eval harness shape, canary rolling baseline, Airflow DAG shelling out to a CLI).
- The new eval is a different shape anyway — per-field precision/recall against hand-labeled extractions, not LLM-as-judge over free text.
- If this ever becomes a business, disentangling it later from a public portfolio repo (history, licensing, what can be open-sourced) is expensive. Separating now costs an afternoon.
- Name it something vertical. "FinAgent" is a platform name and collides with the KDD paper.

**Bonus framing:** FinAgent's README pitch is *"onboard your agent here and get evals, tracing, drift detection, and CI for free"* — currently only a demo agent and a dummy agent are onboarded. Building a real, structurally different product and onboarding it (same eval patterns, same tracing, same CI shape) is a much stronger proof of that claim than adding EDGAR features to the demo agent.

---

## What to build first

Scope: **one vertical, ~20 companies, ~8 KPIs, 8 quarters of history.** Go deep through all six layers before adding a single company.

The artifact that proves the thesis:
1. One peer comparison table where **every cell links to its exact accession, section, period, and quoted source text**.
2. One "what changed this quarter" report generated off the same store.

If the citations don't hold up under spot-checking, nothing downstream matters.

First two commits:
1. **`xbrl_facts`** over `data.sec.gov/api/xbrl/companyfacts/CIK##########.json` (and `companyconcept/.../us-gaap/<Concept>.json`) — free, no API key, exact values with units, fiscal periods, and originating accession. This becomes the Facts layer and structurally eliminates numeric hallucination.
2. **The fact store schema** — `(concept, value, unit, period, accession, form, extracted_by, source_span)` — append-only, versioned. This is the spine everything else hangs off.

## Deferred / lower priority

Real but secondary once the above is in place: 8-K item-code filtering (4.02 restatement, 5.02 exec departure, 1.01 material agreement), Form 4 insider transactions (structured XML), multi-ticker retrieval, 10-K/A amendment awareness, table-aware extraction (parse `<table>` to markdown before text extraction, keep tables intact as chunks), EDGAR full-text search across the corpus via `efts.sec.gov` (2001–present; **confirm exact endpoint shape before relying on it — it was not verified this session**).

## Working style to carry over

From FinAgent's `HANDOFF.md`, and it matters more here: **nothing gets described as working without being run.** For this product specifically, that means citation spot-checks are part of "done" — a claimed source span must be verified to appear verbatim in the cited document.
