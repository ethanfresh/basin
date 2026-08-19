# Commercial compliance: removing the one licensed dependency

**Status: done.** Cohort assignment no longer touches a paid feed. Every input Basin
uses is now a free, public SEC source, and the 91-company cohort reproduces exactly.

---

## Why this change

Basin is intended to be sold. That makes the provenance of every input a commercial
question and not only a technical one, and there were two different kinds of input in
the pipeline:

**Filing data — no issue.** Facts are not copyrightable, EDGAR is free and public, the
SEC imposes no licence on the data, and commercial redistribution of EDGAR-derived
figures is ordinary and widespread. The only obligations are operational: declare a
`User-Agent` and stay under 10 requests per second, both enforced in
[`src/basin/edgar/client.py`](../src/basin/edgar/client.py).

**Industry classification — a problem.** Cohort membership came from the Finviz Elite
screener export API, authenticated with a paid subscription token. That is a contract
question rather than a copyright one, which makes it harder rather than easier: a
subscription's terms do not contemplate redistributing the classification inside a
product sold to someone else, and unlike copyright there is no fair-use defence against
terms you agreed to. It was also the single non-public dependency in an otherwise
entirely public pipeline — one component that a customer, an auditor, or a diligence
process could not independently re-derive.

The SEC assigns every filer a **SIC code**, publishes it free in the submissions API,
and — the part that makes it usable — lets it be enumerated in reverse: every filer
under a code, which is precisely what cohort membership needs.

## What changed

| | Before | After |
|---|---|---|
| Classification source | Finviz Elite screener export | SEC SIC code, via EDGAR |
| Authentication | `FINVIZ_AUTH_TOKEN` (paid) | none |
| Credentials in the whole pipeline | one | zero |
| Cohort members | 91 | 91 (identical) |
| Admission rule | in the Finviz pull | in a producing SIC code **and** a filing confirms reserves |

### Removed

- `src/basin/finviz/` — the client, the CSV export parser, and the `FINVIZ_AUTH_TOKEN`
  environment requirement.
- `_collapse_share_classes()` in `sync_cohorts.py`. It existed because the screener
  returned one row per listed security, so Petrobras arrived twice as `PBR` and `PBR-A`.
  EDGAR's submissions record is per-registrant, so this is structurally impossible now
  rather than corrected after the fact.
- One exclusion: `MHM`, a Bank of America structured note the screener had placed in an
  operating energy industry. It carries no oil & gas SIC code, so enumerating from EDGAR
  never proposes it. That entire class of error does not arise.

### Added

**[`src/basin/cohorts.py`](../src/basin/cohorts.py)** — the classification policy, in one
place, as data rather than as code paths:

- `SIC_COHORTS` — `1311 Crude Petroleum & Natural Gas` → E&P, `6792 Oil Royalty Traders`
  → E&P, `2911 Petroleum Refining` → Integrated. This settles **78 of the 91** members.
- `SIC_OVERRIDES` — the **13** filers whose code does not describe them, one entry each
  with its reason.
- `EXCLUDED` — the **5** filers that sit in a producing code and hold no reserves.
- `NON_OPERATOR_SIC` / `is_operator()` — royalty and minerals vehicles.

### Changed

**`FilerProfile` now tracks annual reports, not 10-Ks.** The recency gate matched only
`10-K`. Twenty of the 91 members file `20-F` or `40-F` and never file a 10-K, so the gate
would have discarded every foreign private issuer in the cohort — Shell, BP, Equinor,
Petrobras, Suncor, Cenovus — and discarded them *silently*, since a filer that fails a
filter simply is not there. `latest_10k_date` / `tenk_count` became `latest_annual_date` /
`annual_count` / `latest_annual_form` across `discovery.py`, `discover_cohort.py`, and the
tests. `sic_ciks()` now sweeps each of the three forms and unions the result.

**Domicile falls back to state of incorporation.** Eight of the 22 foreign-domiciled
members — Petrobras, Eni, Ecopetrol among them — carry no business address in EDGAR at
all, so the address alone reported them as domestic. `FilerProfile.country` reads the
address first and incorporation second, and returns `None` rather than guessing when only
a US incorporation code is available with no address: Shell plc is incorporated `DC` in
EDGAR, and falling through to it would label a UK company American.

**`market_cap_musd` is no longer populated.** The screener exported it; EDGAR publishes no
market capitalisation. The column and its existing values are preserved — `upsert_company`
COALESCEs — but nothing writes it. Nothing in the panel or the KPI schema reads it.

**`cohort_source` now records `sic` or `sic-override`** instead of `finviz`, so a
deviation from the SEC's own classification is visible in the store, not only in source.

## The honest cost: SIC is coarser

A SIC code records what a registrant *registered as*, not what it does, and EDGAR never
revisits it. Finviz classified better. Three specific losses, all paid for explicitly in
`SIC_OVERRIDES` rather than absorbed:

**1. Integrated majors are split across two codes for no visible reason.** The US majors
(XOM, CVX, COP) are `2911`; the non-US ones (Shell, TotalEnergies, Eni, Petrobras,
Ecopetrol, Cenovus) are `1311`, the same code as a single-basin Permian pure-play. Left
uncorrected, Shell joins the E&P cohort and the panel puts an integrated filer's
consolidated per-BOE costs beside a pure-play's — exactly the comparability the schema
exists to refuse. Seven overrides.

**2. Codes go stale and are never corrected.** ConocoPhillips is still `2911 Petroleum
Refining`, which it has not been since it spun off Phillips 66 in **2012**.

**3. Operators land under service codes.** Baytex and HighPeak are `1381 Drilling Oil &
Gas Wells`; both hold their own reserves. National Fuel Gas is `4924 Natural Gas
Distribution` despite the Seneca Resources segment; Sky Quarry is `4955 Hazardous Waste
Management`.

**And one gain.** `6792 Oil Royalty Traders` is EDGAR's own code for royalty and minerals
vehicles, so it settles `is_operator` outright — where the previous rule matched
`"royalt" / "minerals" / "trust"` against the company's name. Texas Pacific Land is the
case that proves it: nothing in its name says royalty, and it was flagged as an operator.
It is now correctly a non-operator, the **only** substantive data change in the migration.

## The structural improvement

Because SIC is noisy, membership can no longer be inherited from the classification — and
that turned out to be the right architecture rather than a concession. **SIC proposes; the
filing disposes.**

SIC 1311 sweeps in shells, midstream partnerships, refiners and — observed in the
population — a biotechnology company. So a candidate now joins the cohort only when
`producer_check` records that its annual report was read and reserves were found. The
gate is disclosure, not a vendor's label.

Candidates that SIC proposes and no filing has confirmed are **held out and reported**,
never admitted quietly:

```
held out of the cohort -- SIC proposes them, no filing has confirmed reserves (33):
  ? SOC      Sable Offshore Corp.                   SIC 1311  no producer verdict (never checked)
  ? VNOM     Viper Energy, Inc.                     SIC 1311  no producer verdict (never checked)
  ? RKDA     Arcadia Biosciences, Inc.              SIC 1311  no producer verdict (never checked)
  ...
  run: python scripts/check_producers.py --apply, then re-run this script
```

Some of those are real E&Ps the previous cohort was missing (Sable Offshore, Viper Energy,
MV Oil Trust, Reserve Petroleum). Some are not energy companies at all. The point is that
the difference is now decided by reading a filing, and the undecided ones are visible
rather than absent.

## Verification

The new classification was replayed offline over 1,402 cached submissions payloads and
diffed against the live store:

```
current members 91 | admitted 91 | held 33
lost:             []
gained:           []
cohort changes:   []
is_operator changes: [('TPL', True, False)]

by cohort: {'Oil & Gas E&P': 75, 'Oil & Gas Integrated': 16}
by source: {'sic': 78, 'sic-override': 13}
```

The SIC path reproduces the cohort exactly. The single change is TPL's operator flag,
which is a correction.

The reverse enumeration was also checked live against `browse-edgar` for SIC 6792: 39
filers returned, of which the recency and listing gates keep 4 — the dead ones (Avoca,
Burlington Resources Coal Seam Gas, last 10-K 1996) are excluded by the gate, not by a
hand-written list. `20-F` filers appear in the sweep, confirming the annual-form union
works against the real endpoint.

The suite passes, with 38 tests over cohort assignment and discovery. New coverage: the SIC→cohort rules, override integrity (every override
targets a real cohort, carries a reason, and is keyed by a padded CIK; no filer is both
overridden and excluded), 20-F/40-F recognition, `10-K/A` counting while `10-KT` does not,
and the incorporation fallback including the Shell case.

## Running it

```bash
export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"

python scripts/sync_cohorts.py                # report; holds out unverified candidates
python scripts/check_producers.py --apply     # adjudicate the held ones
python scripts/sync_cohorts.py --apply        # write membership
```

`--survey` additionally enumerates the non-producing oil & gas codes (drilling, field
services, midstream, distribution, petroleum wholesale) to show what the producing filter
is excluding. `--admit-unverified` bypasses the producer gate and exists mainly to make
the gate's effect measurable.

The first run is slower than the Finviz one was: it enumerates three SIC codes and fetches
a submissions payload per CIK at 8 requests per second, against eight CSV downloads
before. It is also free, unauthenticated, and re-derivable by anyone.

---

## What this does *not* address

This change closes the licensing question on inputs. Three commercial items remain open
and are not code:

1. **Entity and liability.** No LLC, no vendor terms. The real exposure is not the data
   supply chain — it is a customer underwriting a loan off a mis-scaled reserves figure.
   Standard vendor terms handle it: as-is, no warranty of accuracy, liability capped at
   fees paid. Wanted before the first paying customer, not after.

2. **Securities regulation.** Selling data is not investment advice, and impersonal,
   regularly-circulated financial information sits outside the Investment Advisers Act.
   The line to stay behind is personalised recommendations for compensation. Keeping the
   product descriptive — *here is what they reported* — and shipping no buy/sell framing
   keeps it clearly outside. Worth re-checking before the alerts layer ships, since an
   alert is closer to a recommendation than a table is.

3. **University IP.** If any of this was built with BU resources or under funded
   research, their IP policy may have a claim. Usually student work on personal equipment
   is not affected, but it is worth confirming with BU's technology transfer office.

None of the three is a reason not to keep building. All three are cheaper to settle now
than to unwind later — which is the same argument that motivated this change.
