"""Find where a filing's reserve tables are, using the full-text index.

The reserve extractor (:mod:`basin.documents.reserves`) parses every table in a
document and keeps the ones carrying a reserve category. That works, and it
answers the wrong question twice.

**It parses documents that hold no reserve table.** A 10-K runs to several
megabytes and a few hundred tables, almost none of them reserve tables. Worse,
the caller has to guess which document to open, and guessing is what bounds
coverage: reading only the primary document of a 10-K skips every 20-F and 40-F
filer in the cohort, and a 40-F is frequently a cover sheet whose NI 51-101
reserve statements are in an attached Annual Information Form. Cenovus's primary
document is 15,395 characters and carries no reserve disclosure at all.

**It cannot say where it looked.** A parse that returns nothing is
indistinguishable from a document that has nothing, which is the distinction
coverage reporting turns on.

``document_search`` already knows. It is an FTS5 index over 13.2 million lines
of the corpus, built by ``scripts/index_documents.py``, and it can answer "which
documents use reserve-table language, and on which pages" in one query. This
module asks it, and hands the extractor a ranked list of places to look.

## Telling a table row from a sentence

The index is over lines, and ``basin.documents.text`` splits on block
boundaries, so a table cell is its own line. That makes the discrimination
almost free — a reserve-table row label is a short line, and prose is not:

    l5680 p123 [  34] Proved Undeveloped Reserves (PUDs)
    l5682 p123 [  58] Beginning proved undeveloped reserves at December 31, 2024
    l5694 p123 [  55] Ending proved undeveloped reserves at December 31, 2025
    l5681 p123 [ 256] At December 31, 2025, the Company's estimated PUD reserves were …
    l5678 p123 [1078] During the year ended December 31, 2024, the Company's extensions …

Both kinds are counted. Prose hits are not evidence of a table, but a document
with reserve prose and no reserve rows is a real finding — usually a wrapper
form whose tables are in an exhibit — and it is reported rather than dropped.

## One locator, many tables

The mechanism is not specific to reserves. A :class:`TableSpec` names the FTS
query that finds a disclosure and the row labels that identify its categories,
and everything below -- clustering, label-vs-prose, ranking -- is the same for
every metric. Two are defined here: :data:`RESERVES` and :data:`PRODUCTION`,
the Regulation S-K Item 1204 table carrying production volume, average realized
price and production cost per unit. That one table is three panel columns, and
XBRL reaches 41%, 9% and 2% of the cohort for them respectively.

## What a site is

Row labels cluster: a reserve table puts developed, undeveloped and total within
a few dozen lines of each other, and a filing repeats the whole table in Item 2
and again in the supplemental note. Each cluster is one site. A site naming all
three categories is one whose developed + undeveloped = total identity can be
checked, which is the gate the ingest applies downstream — so that is what the
ranking is built on.

This proposes; :mod:`basin.documents.reserves` disposes. A site is a place worth
parsing, never a claim that a number is there.
"""

from __future__ import annotations

import re
import sqlite3
from dataclasses import dataclass

# The FTS5 query. Phrases, not bare terms: "proved" alone matches every
# impairment paragraph in the filing. The tokenizer is porter, so "developed"
# and "development" stem together, which is why the category is confirmed by
# regex below rather than trusted from the index.
#
# "proved plus probable" and "forecast prices" are the NI 51-101 forms. They are
# included because the Canadian filers are exactly the population the previous
# 10-K-only selection lost, and their tables do not say "proved undeveloped".
@dataclass(frozen=True)
class TableSpec:
    """A disclosure worth locating: how to find it, and what its rows say."""

    name: str
    match: str
    """The FTS5 query. Phrases, not bare terms -- "proved" alone matches every
    impairment paragraph in the filing."""

    categories: tuple[tuple[str, re.Pattern[str]], ...]
    """``(category, pattern)``. Deliberately looser than the extractors' own
    row patterns: here a false positive costs one table parsed for nothing,
    while a false negative leaves a filer silently uncovered."""

    closable: frozenset[str] = frozenset()
    """Categories whose presence together means the reading can be checked
    against itself -- the reserve identity, or a price and a volume that must
    multiply to revenue. A site holding all of them outranks one that does not."""


RESERVES = TableSpec(
    name="reserves",
    # "proved plus probable" and "forecast prices" are the NI 51-101 forms.
    # They are included because the Canadian filers are exactly the population
    # a 10-K-only selection loses, and their tables do not say "proved
    # undeveloped".
    match=(
        '"proved developed" OR "proved undeveloped" OR "total proved" '
        'OR "proved plus probable" OR "proved reserves"'
    ),
    categories=(
        ("developed", re.compile(r"(?i)proved[\s,]+developed")),
        ("undeveloped", re.compile(r"(?i)proved[\s,]+undeveloped")),
        ("total", re.compile(r"(?i)(?:total\s+proved|proved\s+plus\s+probable)")),
    ),
    closable=frozenset({"developed", "undeveloped", "total"}),
)

PRODUCTION = TableSpec(
    name="production",
    # Regulation S-K Item 1204 requires production, average sales price and
    # average production cost per unit, for each product, for each of the last
    # three years. Every producer files it; almost none tag it.
    match=(
        '"average sales price" OR "average sales prices" '
        'OR "average production cost" OR "average production costs" '
        'OR "production cost per" OR "sales price per"'
    ),
    categories=(
        # Filers do not all say "sales price". Diamondback heads its column
        # "Average price, hedged ($ per BOE)" and Devon "Realized price,
        # unhedged"; requiring the word "sales" lost both.
        ("price", re.compile(
            r"(?i)average\s+(?:realized\s+|net\s+)?sales?\s+price"
            r"|average\s+(?:realized\s+)?price\b|realized\s+prices?\b"
            r"|sales?\s+price\s+per\b")),
        ("cost", re.compile(
            r"(?i)average\s+production\s+costs?|production\s+costs?\W+per"
            r"|average\s+costs?\s+per\b"
            r"|lease\s+operating\s+(?:expenses?|costs?)\W+per")),
        # The lookahead is load-bearing. Without it "Production Cost (Per Boe)"
        # -- Devon's own row label -- matches as a volume, and a dollars-per-
        # barrel figure is proposed as a production quantity. A category is
        # only useful if it excludes.
        ("volume", re.compile(
            r"(?i)^\s*(?:net\s+|total\s+|annual\s+)*production\b"
            r"(?!\W*(?:cost|expense|price|tax|revenue))"
            r"|\bproduction\s+volumes?\b|\bnet\s+production\b")),
    ),
    # Price and cost together are the pair the panel needs: a realized price
    # without the cost beside it says nothing about a barrel's economics.
    closable=frozenset({"price", "cost"}),
)

SPECS: dict[str, TableSpec] = {spec.name: spec for spec in (RESERVES, PRODUCTION)}

RESERVE_MATCH = RESERVES.match
"""Kept as a name because it reads better at call sites than ``RESERVES.match``."""

# A reserve-table row label is a short line. Measured across the corpus, real
# labels run 26-80 characters ("Proved Undeveloped Reserves (PUDs)" is 34,
# "Beginning proved undeveloped reserves at December 31, 2024" is 58) and the
# prose in the same neighbourhood starts at 208. The threshold sits in the gap
# rather than at either edge of it.
LABEL_MAX_CHARS = 110

# Two row labels this far apart in line numbers are in different tables. A
# reserve table's rows sit within a few dozen lines of each other even with a
# number line between every pair; the gap between the Item 2 table and the
# supplemental note is thousands.
CLUSTER_GAP = 80

CLOSABLE = RESERVES.closable
"""Back-compatible name for the reserve family's checkable set."""


@dataclass(frozen=True)
class ReserveSite:
    """One place in one document where reserve-table rows cluster."""

    document_id: int
    accession: str
    name: str
    """Filename as filed, so the caller can open it from the corpus."""

    cik: str | None
    form: str | None
    filed_date: str | None
    kind: str | None
    """primary | exhibit — a 40-F's tables are usually in an exhibit."""

    first_line: int
    last_line: int
    first_page: int
    last_page: int
    section: str | None
    categories: frozenset[str]
    label_hits: int
    sample: str
    """A row label from the cluster, so a person can see what was matched."""

    table: str = RESERVES.name
    """Which :class:`TableSpec` found it."""

    @property
    def closable(self) -> bool:
        """Whether this site holds every category the self-check needs."""
        return SPECS[self.table].closable <= self.categories

    @property
    def score(self) -> tuple[int, int, int]:
        """Ranking key, best first when sorted in reverse.

        Categories before hits: a site naming developed, undeveloped and total
        once each is a checkable table, and one naming undeveloped forty times
        is a PUD rollforward discussion. Density breaks ties within that.
        """
        return (len(self.categories), self.label_hits, -(self.last_line - self.first_line))


@dataclass(frozen=True)
class DocumentReserveHits:
    """One document's reserve language, before it is grouped into sites."""

    document_id: int
    accession: str
    name: str
    cik: str | None
    form: str | None
    filed_date: str | None
    kind: str | None
    sites: tuple[ReserveSite, ...]
    table: str
    prose_hits: int
    """Reserve sentences with no table row anywhere in the document.

    A document with prose and no site is the wrapper-form signature: the filing
    discusses reserves and the tables are somewhere else.
    """

    @property
    def has_table(self) -> bool:
        return bool(self.sites)

    @property
    def best(self) -> ReserveSite | None:
        return self.sites[0] if self.sites else None


def _categories(text: str, spec: TableSpec) -> frozenset[str]:
    return frozenset(name for name, pattern in spec.categories if pattern.search(text))


def _cluster(
    lines: list[sqlite3.Row], meta: sqlite3.Row, spec: TableSpec
) -> list[ReserveSite]:
    """Group a document's row-label hits into sites."""
    sites: list[ReserveSite] = []
    batch: list[sqlite3.Row] = []

    def flush() -> None:
        if not batch:
            return
        categories: set[str] = set()
        for row in batch:
            categories |= _categories(row["text"], spec)
        if not categories:
            return
        sites.append(
            ReserveSite(
                document_id=meta["id"],
                accession=meta["accession"],
                name=meta["name"],
                cik=meta["cik"],
                form=meta["form"],
                filed_date=meta["filed_date"],
                kind=meta["kind"],
                first_line=batch[0]["line_no"],
                last_line=batch[-1]["line_no"],
                first_page=batch[0]["page"],
                last_page=batch[-1]["page"],
                section=batch[0]["section"],
                categories=frozenset(categories),
                label_hits=len(batch),
                sample=batch[0]["text"][:120],
                table=spec.name,
            )
        )

    for row in lines:
        if batch and row["line_no"] - batch[-1]["line_no"] > CLUSTER_GAP:
            flush()
            batch = []
        batch.append(row)
    flush()

    sites.sort(key=lambda s: s.score, reverse=True)
    return sites


_SELECT = """
SELECT d.id, d.accession, d.name, d.cik, d.form, d.filed_date, d.kind,
       dl.line_no, dl.page, dl.section, dl.text
FROM document_search ds
JOIN document_line dl ON dl.rowid = ds.rowid
JOIN document d ON d.id = dl.document_id
WHERE document_search MATCH ?
"""


def table_hits(
    conn: sqlite3.Connection,
    spec: TableSpec = RESERVES,
    *,
    accession: str | None = None,
    cik: str | None = None,
    forms: tuple[str, ...] | None = None,
    document_id: int | None = None,
) -> list[DocumentReserveHits]:
    """Every document whose text uses *spec*'s table language, with its sites.

    Filters narrow the index query rather than the result, so asking for one
    accession costs one small query rather than a scan of 13 million lines.

    Ordered by the best site each document holds, so a caller taking the first
    result gets the document most likely to carry a checkable reserve table.
    """
    sql = _SELECT
    params: list[object] = [spec.match]
    if document_id is not None:
        sql += " AND d.id = ?"
        params.append(document_id)
    if accession is not None:
        sql += " AND d.accession = ?"
        params.append(accession)
    if cik is not None:
        sql += " AND d.cik = ?"
        params.append(cik)
    if forms:
        sql += f" AND d.form IN ({','.join('?' * len(forms))})"
        params.extend(forms)
    sql += " ORDER BY d.id, dl.line_no"

    by_document: dict[int, list[sqlite3.Row]] = {}
    meta: dict[int, sqlite3.Row] = {}
    for row in conn.execute(sql, params):
        by_document.setdefault(row["id"], []).append(row)
        meta.setdefault(row["id"], row)

    out: list[DocumentReserveHits] = []
    for document_id_, rows in by_document.items():
        labels = [r for r in rows if len(r["text"]) <= LABEL_MAX_CHARS]
        sites = _cluster(labels, meta[document_id_], spec)
        out.append(
            DocumentReserveHits(
                document_id=document_id_,
                accession=meta[document_id_]["accession"],
                name=meta[document_id_]["name"],
                cik=meta[document_id_]["cik"],
                form=meta[document_id_]["form"],
                filed_date=meta[document_id_]["filed_date"],
                kind=meta[document_id_]["kind"],
                sites=tuple(sites),
                table=spec.name,
                prose_hits=len(rows) - len(labels),
            )
        )

    # Documents with a checkable table first, then by their best site.
    out.sort(
        key=lambda d: (d.best.score if d.best else (0, 0, 0)),
        reverse=True,
    )
    return out


def reserve_hits(conn: sqlite3.Connection, **kwargs) -> list[DocumentReserveHits]:
    """:func:`table_hits` for the reserve family."""
    return table_hits(conn, RESERVES, **kwargs)


def production_hits(conn: sqlite3.Connection, **kwargs) -> list[DocumentReserveHits]:
    """:func:`table_hits` for the S-K 1204 production / price / cost table."""
    return table_hits(conn, PRODUCTION, **kwargs)


def documents_to_parse(
    conn: sqlite3.Connection,
    accession: str,
    *,
    spec: TableSpec = RESERVES,
    require_table: bool = True,
) -> list[DocumentReserveHits]:
    """The documents of one filing worth handing to the reserve extractor.

    Every document in the filing that holds a reserve-table site, best first —
    not merely the primary one. This is what reaches a 40-F's Annual Information
    Form, where the reserve statements of a Canadian filer actually are.

    With ``require_table`` off, documents carrying only reserve prose are
    returned too, which is how a caller distinguishes "this filing has no
    reserve disclosure" from "its tables are somewhere this index has not
    reached" — the second is a fetch problem, not a filer one.
    """
    hits = table_hits(conn, spec, accession=accession)
    return [h for h in hits if h.has_table] if require_table else hits
