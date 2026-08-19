# Document lookup: known defects and proposed fixes

> **Status: all nine fixed.** Numbers below are before → after, measured
> against the store, not estimated. Verified facts rose from 669 to **2,059**,
> of which **100% are now located** and **99.9% by reading the filing's own
> markup** rather than searching its text.

How Basin finds a number in a filing, what the original approach got wrong,
what was done, and what remains open. The original eight defects are preserved
below with their measurements, each followed by the outcome; D9 was found later,
by reading the citations the fixes produced.

**The headline held up.** Lookup was a string search and did not need to be:
every primary document is inline XBRL, so each tagged figure is already marked
up with its concept, period, product, unit and presentation scale. Reading the
markup rather than the text removed D1, D3 and most of D7 at once, and turned
the scale resolver from an inference engine into a check.

## What changed, in one table

| | Defect | Before | After |
|---|---|---|---|
| D1 | Ambiguous string matches | 230/669 unique (34%) | **2,052/2,054 unique (99.9%)** |
| D2 | Table headers discarded | none | **1,165 facts carry their column header** |
| D3 | Scale inferred | 1,067 cells resolved | **2,480 cells resolved** |
| D4 | Exhibits unsearched | 609 unsearched | searched after the primary |
| D5 | Broken headings | 27 missing, malformed values | **0 malformed**, 58 of 2,054 missing |
| D6 | Page numbers unmeasured | unknown | **measured: only 37% match the printed folio** |
| D7 | Zero values called absent | 15 `not_found` | **0 `not_found`, 5 `unverifiable`** |
| D8 | Parenthesised negatives | never matched | matched |
| D9 | Contents read as the body | 317 citations under an item they are not in | **0**; 987 citations in all re-sectioned |

## New findings

Three things surfaced only after the fixes went in, and each is worth more
than the defect that exposed it.

**The printed page and the page count disagree far more than expected (D6).**
Of 1,332 facts where both are known, only **37% agree**. Offsets cluster at
+7, +10, +2 and +6 — cover pages and contents that carry no folio. The
citation now quotes the printed number and labels the derived one a sheet
number; 722 located facts sit on pages that print no number at all, and say so.

**Declared scale plus header unit removes the economic inference — but only
together.** CNX is the case: the markup declares `scale="3"`, so the figure is
9,662,144, and the declared unit `Mcfe` would make that 9.7 Bcfe against a
company holding roughly 9.7 *Tcfe*. The column header says `MMcf`. Scale from
the markup and unit from the table give the right answer with no $/BOE test at
all; either alone gives one that is wrong by a thousand.

**A correct fix, over-generalised, manufactured a new error.** Unit correction
was applied to every row in a company-period, including rows whose declared
unit differed from the anchor's. W&T's `ft3` figure was relabelled with the
anchor's unit and resolved to **4.2 × 10¹⁷ BOE** — more than the world holds —
at $97/BOE, which cleared the value-per-barrel band because the standardized
measure carried the same error. Two guards now exist: a correction applies only
to rows sharing the anchor's declared unit, and any volume resolving past 1e11
BOE is rejected rather than published. 63 readings are now rejected on that
ground.

---

## Severity summary

| | Defect | Impact now | Fix |
|---|---|---|---|
| **D1** | Lookup is a string search, not a read of the markup | 439 of 669 matches are non-unique | Read `<ix:nonFraction>` |
| **D2** | Table structure is discarded | Caused the Gulfport unit error | Parse cells with headers |
| **D3** | Presentation scale is inferred, not read | 5,000 cells uncomparable | Read `scale=` |
| **D4** | Only the primary document is searched | 609 exhibits unsearched for facts | Search exhibits too |
| **D5** | Section headings break across lines | 27 facts have no section | Normalise whitespace |
| **D6** | Page numbers are derived, not read | Unquantified, possibly off by a cover page | Compare to printed folio |
| **D7** | Small and zero values are unsearchable | 13 of 15 misses | Require markup, not digits |
| **D8** | Negative numbers in parentheses never match | Unknown | Add `(1,234)` form |
| **D9** | The table of contents is read as the body | A figure on page 11 cited as Item 16 | Discard contents rows |

---

## D1. Lookup is a string search over flattened text

**What happens.** `find_value` formats the stored value at several candidate
scales and searches the flattened document for the resulting string. The first
positional match wins.

**Why it is wrong.** A number in a filing is not unique. The same figure
appears in a table, again in MD&A prose, again in a prior-year comparative
column, and again in the reserve rollforward. The match that wins is the one
that appears earliest, which has no particular relationship to the fact being
cited.

**Measured.** Of 669 located facts, only **230 matched a string that appears
exactly once**. 320 matched a string appearing 2–3 times, 118 appearing 4–10
times, and one appearing more than 10 times. So **66% of citations point at
one of several identical-looking numbers**, chosen by document order.

**Fixed.** Reads the inline XBRL. Every primary document contains markup of
the form:

```html
<ix:nonFraction unitRef="mbbls" contextRef="c-610" scale="3"
  name="srt:ProvedDevelopedAndUndevelopedReservesNet" id="f-1841">1,069,508</ix:nonFraction>
```

That element states the concept, the context (period *and* product dimension),
the unit, the presentation scale, and a stable `id`. Matching a stored fact to
its `ix` element by concept + context is exact, and the element's position in
the document gives the page and line directly. No searching, no ambiguity, and
the citation can address `#f-1841` in the filing rather than a page.

**Outcome.** 2,052 of 2,054 located facts now resolve by markup, each to a
single unambiguous element, addressable as `#f-1841` in the filing itself. Two
resolve by the text fallback, which remains for figures the filer never tagged
— and for the extraction layer, whose fields (realized price, LOE per BOE) are
prose rather than markup.

---

## D2. Table structure is discarded

**What happens.** `parse` flattens block elements to lines. A table row becomes
a line of numbers with no attachment to its column header.

**Why it is wrong.** In a filing, the header *is* the unit and often the
period. Gulfport's reserve table reads:

```
Oil (MMBbl)   Natural Gas (Bcf)   NGL (MMBbl)   Total (Bcfe)
Total proved      19                  2,906           52          3,328
```

Flattened, `4,253` is a bare number. The column that gives it meaning is 40
characters earlier and structurally unrelated.

**Measured.** This produced a real error: Gulfport's total proved reserves were
stored as 4.25 billion BOE (tagged `bbl`) when the correct figure is 708.8
million (the table says `Bcfe`) — a **6× overstatement** that survived until
header parsing was added as a patch.

**Fixed.** `basin.documents.tables` parses tables as tables and attaches each
cell's column header. Gulfport's `4,253` now resolves to header
`Total (Bcfe)`, row `Total proved`; its `3,612` to `Natural Gas (Bcf)`.
1,165 of 2,054 verified facts carry a header, and it feeds unit correction
directly.

Two things this needed that were not obvious. Headers align to *numeric column
position*, not raw cell index, because a data row carries a leading label cell
the header row does not. And a blank spacing row at the top of a table was
flipping the parser into "data" mode, after which every header row was read as
data and no cell got a header at all.

---

## D3. Presentation scale is inferred rather than read

**What happens.** Verification determines the scale by trying the value at
1×, 10³×, 10⁶×, 10⁹× and seeing which one appears in the document. That leaves
two candidate readings, and an economic identity — standardized measure ÷
reserves, tested against a plausible $/BOE band — picks between them.

**Why it is wrong.** It is inference standing in for a value the document
states outright. `scale="3"` is *in the markup*. The whole apparatus of bands,
midpoints and rejected readings exists to recover a number that was never
missing.

**Measured.** Only **1,067 of 6,063 current cells** have a resolved magnitude.
The rest are shown unranked because the inference could not decide. Reading
`scale` would resolve every tagged fact directly.

**Fixed.** `scale` is read from the `ix` element and fixes the divisor;
the $/BOE test now only chooses between *units*, which is the job it is
actually good at. Resolved cells rose from 1,067 to **2,480**.

The warning held: the markup is reliable for scale and not for unit. Gulfport
tags 3,612 Bcf of **gas** as `unit="bbl"` in its own inline XBRL, so unit
correction stays exactly where it was.

---

## D4. Only the primary document is searched

**What happens.** Verification resolves the filing's primary document and
searches that. Exhibits are stored but never searched for facts.

**Why it is wrong.** Guidance and per-unit costs are announced in the earnings
release attached to an 8-K as EX-99.1, not in the 8-K itself. Those are exactly
the fields XBRL does not cover.

**Measured.** **609 exhibits are indexed** (1.6M lines) and contribute nothing
to verification. Of the stored EX-99 documents, 102 contain guidance language
and 73 contain LOE or per-BOE prose.

**Fixed.** Every stored document in an accession is searched, primary first,
so a figure appearing in both still cites the filing proper. The document that
carried the match is recorded.

---

## D5. Section headings break across lines

**What happens.** `section_of` matches `Item N.` headings with a regex anchored
to a line.

**Why it is wrong.** Filings put the item number and its title in separate
table cells, which become separate lines. The captured heading is then
`'Item\n1.\nBusiness'`, and where the split is unlucky the pattern misses
entirely.

**Measured.** **27 of 669** located facts have no section at all, and captured
sections include `'Item 16.\nForm 10-K Summary'` and `'Item\n15.\nExhibits'`.

**Fixed.** The pattern matches across the break and normalises whitespace, and
a canonical `Item 1` is derived alongside the display title. Malformed values
are gone (0 of 2,054). 58 facts still have no section — cover pages, exhibits
and financial statements genuinely sit outside any Item heading, so this is
now a floor rather than a bug.

---

## D6. Page numbers are derived, not read

**What happens.** Pages are counted by splitting on `<hr>` elements carrying
`page-break-after`, so page *n* means "the nth page break".

**Why it might be wrong.** That is the nth *rendered* page, which need not
equal the number printed on it. Cover pages and the table of contents are
often unnumbered or numbered in roman, so a printed folio may sit several
pages behind the count. A reader told "page 46" opens a PDF at page 46 and may
find something else.

**Measured — and it was real.** Of 1,332 facts where both are known, only
**497 (37%) agree**. The rest differ, clustering at +7, +10, +2 and +6:
unnumbered cover pages and contents. Diamondback's total proved sits on the
122nd sheet, which prints **115**.

**Fixed.** The printed folio is extracted and stored, citations quote it, and
the derived count is labelled a sheet number. Where a page prints no number —
722 located facts — the citation says so rather than implying a folio.

---

## D7. Small and zero values are unsearchable

**What happens.** Candidate strings shorter than three digits are refused,
because a two-digit number matches half a filing.

**Why it is wrong.** The refusal is correct for a string search and wrong as a
verdict. A fact whose value is 0 or 7 is recorded as `not_found`, which reads
as "this number is not in the filing" when it means "this method cannot look".

**Measured.** **13 of 15** unverified facts are zero or below 100.

**Fixed both ways.** Markup lookup addresses a fact by concept rather than by
digits, so a zero-valued tagged fact now verifies normally. What remains
genuinely unsearchable is recorded as `unverifiable`, a separate state from
`not_found`. There are now **0 `not_found` and 5 `unverifiable`**.

---

## D8. Negative numbers in parentheses never match

**What happens.** Candidates are generated as `-1,234`. Filings write negatives
as `(1,234)`.

**Measured.** Unquantified — no negative-valued concept is currently ingested,
so this is latent rather than active. It becomes live the moment revisions,
production declines or cash-flow components are added, all of which are
routinely negative.

**Fixed.** Both forms are generated, and a parenthesised match reads as
negative. Still latent — no negative-valued concept is ingested yet — but it
will be live the moment revisions or production declines are added.

---

## D9. The table of contents is read as the body

**What happens.** `section_of` returns the last `Item N.` heading before the
figure's offset.

**Why it is wrong.** A 10-K lists every item on its contents page, before the
body starts. So for anything printed before the first real heading, the last
preceding heading is the last row of the *contents* — Item 16 for a 10-K,
Item 6 for a 10-Q. EQT's proved reserves summary sits on page 11 and was cited
as "Item 16. Form 10-K Summary": the string occurs at offset 5,798 in the
contents and again at 606,236, where the section actually is.

**Measured.** **317 of 9,455** located citations across **173 documents** were
attributed to an item they are not in. The two headline cases are a 10-K's
Item 16 and a 10-Q's Item 6 — both the last row of the contents.

**Fixed.** A contents row is recognisable on its own: it carries the page it
points to, either at the end of its line ("Business .... 8") or on the next
line. One such row proves nothing — an empty "Item 6. [Reserved]" can sit just
above a printed folio — so rows are only discarded where four or more run
together. Two layouts complicate the run: the contents restart their numbering
at each Part (Chesapeake lists 1, 2, 3, 4 and then 1, 1A, 2 … 6), and a real
heading can sit directly under the block above its own folio (Comstock's
Item 1), which is a contents row in every respect except that it is the
section. The last restart in a block is read as the second when it is too
short to be another Part.

Of the 317, **104 moved to the right item** and **213 to no section at all** —
those are filings whose body headings the pattern never matched (`P ART I` /
`I tem 1.`, letter-spaced by the filer), where the only headings in the
document were the contents. `None` is the honest answer there, and the
citation drawer omits the line rather than naming a section the figure is not
in.

**Re-recorded, and the count is larger than the defect.** The same pass fixed
the pattern's whitespace: the gap between an item number and its title was
capped at four whitespace characters, and EQT sets its headings with eight
non-breaking spaces, so a filing's *entire* body index could go unmatched and
leave only its contents to cite from. Re-deriving every stored section from
the corpus — no fetching, the offset and document are already recorded — moved
**774 of 9,455 citations to a different item** and **213 to none**, 8,468
unchanged. EQT's proved reserves summary now reads "Item 1. Business".

**Not fixed, and visible in the same measurement.** A section span that runs
from page 1 is usually not this defect but a page defect: filings that separate
pages with CSS rather than `<hr>` parse as a single page, so every citation
into them reads "page 1". And financial statements bound after the signature
page sit physically inside Item 15/16, so the last-heading rule labels them
Item 16 correctly-by-position and wrongly-by-substance. Both are D6's territory,
not this one.

---

## Still open

**Unit labels: now corrected by document evidence for 1,272 cells.** The
header's unit tokens (not its prose — "Natural Gas Equivalent (Bcfe)" is a
fine display string and a useless unit candidate) feed the resolver as
candidates, and a reading the document states beats the one the filer tagged.
Resolved company-periods rose from 328 to 342, ambiguous fell from 127 to 114,
and canonical cells from 2,480 to 2,601. The tagged unit remains wrong in the
markup for several filers, so this correction stays load-bearing rather than
belt-and-braces.

**114 company-periods remain ambiguous** and 43 unavailable, so their cells
render unranked. Most lack a standardized measure to test against.

**Table header coverage: 57% → 75%.** Diagnosing the missing 43% found two
textual bugs, not a rendering problem. A header row of bare years ("2024
2023") is entirely numeric and was classified as data, taking the real header
with it — the single largest cause. And `header_for_value` searched all tables
and returned the first string match anywhere in the document, so a fact could
be assigned the header of an unrelated balance-sheet table; matching is now
positional, preferring the table that contains the figure's markup offset.
The remaining 25% are figures genuinely stated in prose.

**The check was done against the rendered page, not just the text.** A
`/debug/page` endpoint renders any sheet of any stored filing as the filer
wrote it. Looking at Gulfport's sheet 96 and Diamondback's sheet 122 confirmed
the coordinates land on the right page, the header alignment matches what a
reader sees ("Oil (MBbls) · Natural Gas (MMcf) · Natural Gas Liquids (MBbls) ·
Total (MBOE)"), and the product-split figures read exactly as ingested. It
also surfaced two things the text alone hid: the same value appears in
*different tables with different headers* (the region table's "Total (Bcfe)"
against the rollforward's "Natural Gas Equivalent (Bcfe)"), which is why
positional matching matters; and reserve tables are full of parenthesised
negatives, confirming D8 goes live with rollforward concepts.

**Two coordinate bugs found by looking.** Stripped `<head>`/`<style>` regions
shrank the cleaned text, so raw-markup offsets and text offsets were drifting
apart (96 characters today; a large embedded stylesheet would move a citation
onto the wrong page) — dropped regions are now blanked to equal length, making
the two coordinate systems identical by construction. And the hidden
`<ix:header>` XBRL preamble was being treated as page content, putting its
machine-readable text on "sheet 1" and making it findable by search; it is
now blanked like `<head>`.

**The 6:1 gas conversion is still a convention**, applied wherever a gas volume
becomes BOE, and labelled as such on the cell. Nothing here changes that.

## What is deliberately not on this list

**A vector index.** Retrieval by similarity is a recall mechanism. Reserve
tables are near-identical in embedding space, a nearest-neighbour hit is not a
citation, and every defect above is a *precision* problem. A vector index is
worth adding later for finding sections whose wording is unpredictable, feeding
candidates into extraction — never as the source of a number.
