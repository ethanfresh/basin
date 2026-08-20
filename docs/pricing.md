# Pricing: what access to this dataset is worth

**Status: a recommendation, not a decision.** No price has been tested against a real
buyer. Everything below reasons from the coverage numbers and the cohort, both of which
are measured, to price points that are not.

---

## The question

Is $99/month a reasonable price for access to Basin?

It is not crazy. It is wrong in both directions at once, and the two errors resolve at
different moments:

**$99 is too low for the buyer Basin is aimed at, and too high for what Basin ships
today.** Those are not contradictory, they are sequential. The buyer in the README —
smaller investment firms, lenders, consultants, corporate development, accounting — is
replacing a seat that costs tens of thousands a year. For them $99 is not a discount, it
is a signal that nobody's reputation should ride on the output. Meanwhile the two fields
that would actually make them switch, average realized price and production cost per BOE,
are not extracted yet.

So: do not anchor at $99. Build the ladder now, switch on billing when the extraction
layer clears its golden set.

## Why $99 is too low for the stated buyer

A firm carrying a $30,000 terminal line item does not experience $99 versus $600 as a
price difference. Both sit under the threshold where anyone thinks hard. What they do
experience is procurement friction — a vendor form, a security questionnaire, a card on
file — and that friction is identical at either price. Pricing at $99 leaves most of the
willingness-to-pay on the table and buys back nothing in sales effort.

Worse, it argues against the product. The differentiator is the citation on every cell:
accession number, form, fiscal period, page, line, and the verbatim source text — 98% of
9,502 current cells located in the document they cite. That property is exactly what
enterprise buyers pay a premium for, because their own credibility rides on the number
surviving a skeptical reader. Cheap data reads as unverified data. Charging a fiftieth of
the pain removed undercuts the one claim that makes Basin worth switching to.

The arithmetic does not close either. The addressable universe for US E&P specifically is
narrow — plausibly a few thousand seats worldwide.

| Price point | ARR at 100 subs | Subs needed for $1M | What that headcount implies |
|---|---:|---:|---|
| $99 / mo | $118,800 | 842 | Larger than the plausible market |
| $500 / mo | $600,000 | 167 | Reachable by one person selling directly |
| $2,000 / mo | $2,400,000 | 42 | Forty-two logos, named in advance |

The pipeline behind this — 91 filers, 4,269 documents, 13.2M indexed lines, all of it
re-verified as filings arrive — has a real quarterly maintenance cost. At $99 you would
need close to the entire market just to fund the thing that makes the product true.

## Why $99 is too high for what ships today

The panel currently delivers reserves, capex and oil & gas revenue — largely the fields
XBRL already carries. A determined analyst pulls those from `companyfacts` free in an
afternoon. The fields that are genuinely worth money are the ones nobody tagged.

| Panel column | In XBRL | Locatable | Status |
|---|---:|---:|---|
| Capital expenditure | 71 / 89 | — | free elsewhere |
| Oil & gas revenue | 71 / 89 | — | free elsewhere |
| Total proved reserves | 49 / 89 | 86 / 89 | shipped |
| Standardized measure | 47 / 89 | 87 / 89 | shipped |
| **Average realized price** | **8 / 89** | **68 / 89** | **not extracted** |
| **Production cost per BOE** | **2 / 89** | **68 / 89** | **not extracted** |

Both untagged fields are mandated by Regulation S-K Subpart 1200, both are printed in
every one of those filings, and the full-text index finds the table carrying them in 68 of
89. That gap is the product. Until it is closed, a subscriber is paying for a
better-cited version of something free. Coverage figures are measured across the 89
producer-verified cohort members; the current extraction shortfall is enumerated in
[`panel-gaps.md`](panel-gaps.md).

**The recurring-payment problem.** Three features create willingness to pay *again next
month*, and none are built: Excel export with citations preserved, "what changed" diffs
against the prior filing, and alerts when guidance moves or a filer redefines a metric.
Without them, month two shows the subscriber the same table as month one. That is a churn
machine at any price — $99 does not fix it, it makes the churn cheap to ignore.

## The recommendation: three rungs

Ladder it, and let the free tier do the trust-building the citations are uniquely good at.
Each rung exists because the one below it created the conditions for the sale.

### Rung one — Open · free, web only

- The consolidated panel: every producer, every KPI, every period
- Full citations — click any value, open the filing at that page and line
- Peer comparison and reported history, on screen
- **No export of any kind.** The data leaves in a browser tab or not at all.

*Its job:* prove the citations are real to a skeptic who has not paid anything. This is
the marketing, and no competitor copies it without doing the same verification work.

### Rung two — Analyst · $400–600 / seat / month

- Everything in Open
- Excel export with citations preserved in the cells
- Full reported history per company, split by product and unit
- Realized price and production cost per BOE across the cohort

*Its job:* convert the moment the free panel creates. Export is the natural paywall — the
output has to survive being forwarded to someone skeptical, and that is what they are
buying.

### Rung three — Team · $1,500–2,500 / month

- Everything in Analyst, multi-seat
- Alerts: guidance moves, cost metrics shift, definitions change
- "What changed" reports — newest filing diffed against the prior one
- API access against the fact store

*Its job:* carry the business. Alerts are the only feature here a customer cannot
reproduce by working harder, which is what makes them defensible and what makes the price
hold at renewal.

**If a $99 tier is wanted anyway:** make it annual-only, single seat, aimed at the solo
consultant who genuinely cannot clear $500/month. Do not let it become the anchor everyone
negotiates against, and do not launch with it as the headline number. A price is very hard
to raise and very easy to lower.

## When to switch on billing

The gate is not a date. All four should be true before the first invoice:

1. The golden set exists and the extraction layer clears it on **realized price** and
   **LOE per BOE**, per-field, against hand labels.
2. Excel export ships with citations intact in the cells — the forwardability that
   justifies rung two.
3. The filing watcher runs, so the panel is current without anyone remembering to refresh
   it. A stale paid panel is worse than a free one.
4. At least one "what changed" report has been generated and read by someone else.

Until then, run the free panel publicly. It costs almost nothing to serve, it builds the
audience the paid tiers convert from, and it gets the verification claim tested by
strangers while the stakes are still low.

## What is in Basin's favour

Per [`commercial-compliance.md`](commercial-compliance.md), the pipeline has zero licensed
inputs and zero credentials. Cohort assignment moved off the Finviz Elite screener onto
SEC SIC codes and the 91-company cohort reproduced exactly. Facts are not copyrightable,
EDGAR imposes no licence, and commercial redistribution of EDGAR-derived figures is
ordinary.

That means no revenue share, no redistribution clause, no per-seat passthrough, and no
vendor who can reprice you. Gross margin is whatever you charge minus compute. For a data
product that is rare — most competitors resell someone else's feed and price backwards
from what it costs them.

It also means a customer, an auditor, or a diligence process can independently re-derive
every input. That is a sales asset, not only a compliance one, and it belongs in the pitch
at every rung.

## Before committing to a number

1. **Ask five target buyers what they pay today.** Not what they would pay for Basin —
   what is on their current invoice, and what they gave up to afford it. The gap between
   that number and $99 is the whole argument, and it should be evidence rather than
   inference.
2. **Find out whether the buyer is the seat or the firm.** If a consultant expenses it
   personally, per-seat pricing works. If it goes through a firm's data budget, this is a
   site licence and the ladder needs a fourth rung with a negotiated number on it.
3. **Check whether alerts or export is the real hook.** The ladder assumes export converts
   and alerts retain. If alerts turn out to be what people actually want, they belong one
   rung lower and the structure shifts down with them.

Two items from [`commercial-compliance.md`](commercial-compliance.md) become live at the
same moment as the first invoice, and are cheaper to settle before it: vendor terms
capping liability at fees paid, and re-checking the securities-regulation line before the
alerts layer ships, since an alert sits closer to a recommendation than a table does.
