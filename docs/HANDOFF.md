# Basin — session handoff

*Written 2026-08-18, at commit `bb0f4c6`. Supersedes `EDGAR-INTELLIGENCE-HANDOFF.md`
(the pre-code planning doc, kept for history). Read `README.md` first for the
product thesis; this doc is the state of the build and what to do next.*

---

## Where the project stands

Everything in the README's "Facts layer" is built and running, plus most of
what was scheduled after it. 23 commits, 116+ tests, all green.

| | Count |
|---|---|
| Companies (full SIC-1311 cohort) | 94 |
| Fact rows (append-only) | 13,081 |
| Current cells (`fact_current`) | 6,063 |
| Filings registered | 2,049 |
| Documents in corpus (raw HTML, `data/corpus/`, 1.9 GB) | 1,472 accessions |
| Documents indexed (page/line/section per line) | 1,816 · 3.57M lines |
| Facts verified against their cited filing | 2,054 of 2,059 (100% located, 99.9% via inline-XBRL markup) |
| Cells with a resolved canonical magnitude (BOE/USD) | 2,601 |
| Product-split reserve cells (oil/gas/NGL) | 2,053 across 50 companies |

The dashboard (FastAPI, port auto-assigned, `python -m basin.web`) has five
surfaces: Panel (peer table, normalize toggle, click-to-cite), Trends (KPI
line charts), Coverage (companies × concepts), Companies, Data quality.
Clicking any verified value opens a citation drawer: filing, document, page
(printed folio), line, Item heading, quoted line, `#f-NNNN` anchor into the
inline XBRL.

## The one thing in flight

**The vision-model check is built but has never run** — this machine has no
API credentials. `scripts/vision_check.py` renders sheets with headless
Chrome (`basin/documents/render.py`) and asks `claude-opus-5` (structured
output, low effort) whether the parser's header/unit/folio match what a
reader sees, stratified over three groups: no-header facts, unit-corrected
facts, control. Results land in the `vision_check` table.

To run: the user exports `ANTHROPIC_API_KEY` in their shell profile
(`~/.zshenv`), then `python scripts/vision_check.py --per-group 12`
(~$1–2). Afterwards: fold agreement rates into
`docs/document-lookup-issues.md` and surface disagreements in Data quality.
The user chose the API-key route in the last session but had not yet added
the key.

## Pipeline order (a full rebuild)

```bash
export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"   # SEC requires it
python scripts/discover_cohort.py                  # SIC-1311 census (cached)
python scripts/ingest_xbrl.py $(sed 's/^/--cik /' data/cohort_ciks.txt | tr '\n' ' ')
python scripts/load_cohort.py                      # tickers, operator flags
python scripts/fetch_filings.py --per-company 6    # 10-K/10-Q/8-K corpus + EX-99
python scripts/index_documents.py                  # page/line/section + FTS5
python scripts/verify_facts.py --limit 3000        # locate every fact in its filing
python scripts/ingest_product_volumes.py           # oil/gas/NGL from inline XBRL
python scripts/resolve_scales.py                   # canonical magnitudes
python scripts/vision_check.py --per-group 12      # needs ANTHROPIC_API_KEY
```

Order matters: verification needs the corpus, resolution needs verification
(declared scale + header units), product volumes need `filing` rows.
Everything except the two SEC fetches runs offline from `data/`.
**A `rm data/basin.db` rebuild loses the 10-Q/8-K filing registrations until
`fetch_filings.py` re-runs (cached, no re-download) — this bit us once.**

## Hard-won findings (do not re-learn these)

1. **`companyfacts` is a flattened, lossy view.** It drops every XBRL
   dimension (the oil/gas/NGL split is *absent*, not untagged), and it
   silently merges taxonomy migrations. The filings themselves are inline
   XBRL — `<ix:nonFraction>` carries concept, context (period + product),
   unit, and **`scale=`** in place. Prefer the document over the API for
   anything dimensional.
2. **Scale and unit are separate lies.** `scale` is declared in markup and
   trustworthy. The **unit label is not** — Gulfport tags 3,612 Bcf of gas
   as `unit="bbl"` in its own markup. Unit truth comes from the table header
   ("Total (Bcfe)"), checked by the $/BOE band against standardized measure.
3. **Never rank across declared units without resolution** — filers disagree
   on whether the value carries the unit's prefix (Diamondback stores base
   units under `MBoe`; Devon scales to match `MMcfe`).
4. **Alias choice is per-filer, decided by arithmetic** (`developed +
   undeveloped = total`), not by a global preference order.
   `alias_validation` stores the evidence; `drifted` = held historically,
   fails the latest period (Continental, Murphy).
5. **Filenames are never a signal.** Primary doc from `primaryDocument`;
   exhibits from the filing index's *Type column* (`EX-99.1` can be
   `ex_967513.htm` or `decresponseannouncementv28.htm`).
6. **Product from unit only for rates** (`USD/bbl` → oil). Volume units are
   aggregates; labelling them once split EOG into two false panel rows.
7. **Printed folio ≠ sheet number** — only 37% agree; offsets cluster +2…+10
   (unnumbered covers/TOC). Citations quote the folio; `page` is the sheet.
8. **Two silent-wrong-answer classes fixed at schema level:** SQLite
   expression indexes returned NULL columns under certain plans (now stored
   generated columns), and NULL≠NULL broke unique dedup (same fix).
9. **Corrections must be scoped.** Applying an anchor's unit correction to
   the whole company-period relabelled W&T's `ft3` and produced 4.2e17 BOE
   at $97/BOE — inside the sanity band because the error cancelled. Guards:
   corrections apply only to rows sharing the anchor's declared unit; any
   volume >1e11 BOE is rejected (63 currently are).
10. **Headless Chrome on this Mac:** needs its own `--user-data-dir` (else
    it deadlocks with desktop Chrome) and never exits after `--screenshot` —
    poll for the file, then kill.

## Data-quality surface (all deliberate, none silently fixed)

`reserve_consistency` (dev+undev=total; the sum check localises a bad alias),
`unit_discontinuity`, `fact_collision`, `alias_validation` (drifted /
incoherent), `fact_verification` (`unverifiable` ≠ `not_found`), rejected
implausible magnitudes. The store never rewrites a filer's value —
corrections live beside the value (`fact_scale`) with basis + rejected
alternatives.

## Likely next steps, in rough priority

1. **Run the vision check** (blocked only on the key), fold results into
   `docs/document-lookup-issues.md`, wire disagreements into Data quality.
2. **Extraction layer** — the commercial gap: realized price (2/100 in XBRL)
   and LOE per BOE (0/100). The corpus + FTS5 index already locate the prose
   (`"lease operating expense per Boe"` → OXY 10-K p.46 Item 7; 102 EX-99s
   contain guidance language, 73 LOE/per-BOE). Architecture rule: recall via
   FTS/vectors, precision via schema-constrained extraction, verify verbatim
   against `document_line`, `extracted_by='llm:<name>'` requires
   `source_span` (schema-enforced CHECK).
3. **Golden set** — hand-label fields × companies × periods; the README calls
   this the product's core asset. The verified/located facts are a strong
   seed.
4. **Cohort lock** — filter the 94 to real operators (~40–45; the
   `is_operator` heuristic in `load_cohort.py` is a placeholder), pick 20
   across basins. Coverage ranking alone selects royalty vehicles — don't.
5. **Filing watcher** — poll submissions for new filings → incremental
   ingest → change detection ("what changed" reports are a README promise).
6. Remaining lookup gaps: 114 ambiguous company-periods (mostly no
   standardized measure), header coverage 75% (rest is prose), sibling-table
   headers unhandled.

## Environment notes

- Python 3.11 venv at `.venv` (uv). `BASIN_SEC_USER_AGENT` required for any
  EDGAR call; rate limit 8 req/s built into `EdgarClient`.
- `data/` is gitignored and fully regenerable; `data/cache/` (submissions,
  companyfacts) makes re-runs cheap.
- Dashboard launches via `.claude/launch.json` (`basin-web`, autoPort; the
  app reads `PORT`). Server must be restarted to pick up Python changes (no
  --reload).
- Tests: `pytest` — offline, fixture-driven; the plan-independence tests in
  `tests/test_schema_integrity.py` guard the silent-wrong-answer class.
