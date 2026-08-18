# Document lookup: known defects and proposed fixes

How Basin currently finds a number in a filing, what that approach gets wrong,
and what to do about it. Every figure below is measured against the current
store (669 verified facts, 1,794 indexed documents, 3.5M lines), not estimated.

**The headline: the current lookup is a string search, and it does not need to
be.** Every primary document is inline XBRL, so each tagged figure is already
marked up in place with its concept, period, unit and presentation scale.
Switching from "search the text for a number that looks right" to "read the
markup that says which number this is" removes the top four defects at once.
That is [D1](#d1-lookup-is-a-string-search-over-flattened-text), and it should
be done before the rest.

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

**Fix.** Read the inline XBRL instead. Every primary document contains markup
of the form:

```html
<ix:nonFraction unitRef="mbbls" contextRef="c-610" scale="3"
  name="srt:ProvedDevelopedAndUndevelopedReservesNet" id="f-1841">1,069,508</ix:nonFraction>
```

That element states the concept, the context (period *and* product dimension),
the unit, the presentation scale, and a stable `id`. Matching a stored fact to
its `ix` element by concept + context is exact, and the element's position in
the document gives the page and line directly. No searching, no ambiguity, and
the citation can address `#f-1841` in the filing rather than a page.

**Caveat.** This only covers figures the filer tagged. Anything the extraction
layer pulls from prose — realized price, LOE per BOE — still needs text search,
so `find_value` remains, demoted to a fallback.

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

**Fix.** Parse tables as tables: a `document_table` / `document_cell` pair
recording row, column, and the header text governing each column. Then the
unit of a cell is a lookup rather than a proximity heuristic, and D1's context
matching gains a cross-check.

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

**Fix.** Take `scale` from the `ix` element. Keep the $/BOE test, but demote it
from *resolver* to *check*: if the stated scale implies an absurd value per
barrel, that is a finding worth surfacing, not a number to overrule.

**Do not** discard the unit-correction logic. Scale and unit are separate
problems, and the markup is only reliable for the first: Gulfport tags 3,612
Bcf of **gas** as `unit="bbl"` in the markup itself.

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

**Fix.** Search every document in an accession, ranked primary-first, and
record which document the match came from — the schema already has the column.

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

**Fix.** Normalise internal whitespace before matching, allow the number and
title to be adjacent lines, and store the canonical form (`Item 1`) separately
from the display title.

---

## D6. Page numbers are derived, not read

**What happens.** Pages are counted by splitting on `<hr>` elements carrying
`page-break-after`, so page *n* means "the nth page break".

**Why it might be wrong.** That is the nth *rendered* page, which need not
equal the number printed on it. Cover pages and the table of contents are
often unnumbered or numbered in roman, so a printed folio may sit several
pages behind the count. A reader told "page 46" opens a PDF at page 46 and may
find something else.

**Measured.** Not yet quantified — this is the one item here that is a
suspicion rather than a finding. Filings do print the folio in the text near
each break (the sequence `... development plan. 6 Table of Contents ...` is a
page number followed by the next page's header).

**Fix.** Extract the printed folio adjacent to each break, compare it to the
derived count across the corpus, and report the offset distribution. If they
agree, say so; if they diverge, store the printed folio and label the derived
one as a sequence number.

---

## D7. Small and zero values are unsearchable

**What happens.** Candidate strings shorter than three digits are refused,
because a two-digit number matches half a filing.

**Why it is wrong.** The refusal is correct for a string search and wrong as a
verdict. A fact whose value is 0 or 7 is recorded as `not_found`, which reads
as "this number is not in the filing" when it means "this method cannot look".

**Measured.** **13 of 15** unverified facts are zero or below 100.

**Fix.** Under D1 these become verifiable, since the markup is addressed by
concept rather than by digits. Until then, record them as `unverifiable` rather
than `not_found`, so the two states are not conflated.

---

## D8. Negative numbers in parentheses never match

**What happens.** Candidates are generated as `-1,234`. Filings write negatives
as `(1,234)`.

**Measured.** Unquantified — no negative-valued concept is currently ingested,
so this is latent rather than active. It becomes live the moment revisions,
production declines or cash-flow components are added, all of which are
routinely negative.

**Fix.** Generate both forms, and treat a parenthesised match as negative.

---

## Suggested order

1. **D1 + D3 together** — one change, reading `ix` markup, fixes both. Largest
   correctness gain per unit of work, and it shrinks the scale-inference code
   to a verification check.
2. **D4** — cheap, and it unlocks the exhibits where the commercially valuable
   fields live.
3. **D2** — the most work, and the prerequisite for trusting units without the
   $/BOE fallback.
4. **D5, D7, D8** — small correctness and honesty fixes.
5. **D6** — measure first; it may be a non-issue.

## What is deliberately not on this list

**A vector index.** Retrieval by similarity is a recall mechanism. Reserve
tables are near-identical in embedding space, a nearest-neighbour hit is not a
citation, and every defect above is a *precision* problem. A vector index is
worth adding later for finding sections whose wording is unpredictable, feeding
candidates into extraction — never as the source of a number.
