"""Locating reserve tables through the full-text index.

The locator proposes places worth parsing; :mod:`basin.documents.reserves`
disposes. These tests pin the discriminations that make the proposal worth
anything -- telling a table row from a sentence about one, keeping two tables in
one document apart, and ranking a checkable table above a rollforward
discussion.
"""

from __future__ import annotations

import pytest

from basin.documents.sites import (
    CLUSTER_GAP,
    LABEL_MAX_CHARS,
    RESERVE_MATCH,
    documents_to_parse,
    reserve_hits,
)
from basin.store import connect


@pytest.fixture
def conn(tmp_path):
    return connect(tmp_path / "t.db")


def _document(conn, document_id, accession, name, *, form="10-K", kind="primary",
              cik="0000000001"):
    conn.execute(
        "INSERT INTO document (id, accession, name, cik, form, filed_date, kind) "
        "VALUES (?, ?, ?, ?, ?, '2026-02-20', ?)",
        (document_id, accession, name, cik, form, kind),
    )


def _lines(conn, document_id, rows, *, page=1, section="ITEM 2. PROPERTIES"):
    """rows: (line_no, text)."""
    conn.executemany(
        "INSERT INTO document_line (document_id, line_no, page, section, "
        "char_offset, text) VALUES (?, ?, ?, ?, ?, ?)",
        [(document_id, n, page, section, n * 40, text) for n, text in rows],
    )


def _reindex(conn):
    conn.execute("INSERT INTO document_search(document_search) VALUES('rebuild')")


# A reserve table as the line parser produces it: a cell per line, so a row
# label and its figures are separate lines.
TABLE = [
    (100, "Proved developed reserves"),
    (101, "163,700"),
    (102, "1,069,700"),
    (103, "Proved undeveloped reserves"),
    (104, "70,300"),
    (105, "412,300"),
    (106, "Total proved reserves"),
    (107, "234,000"),
]

PROSE = (
    200,
    "During the year ended December 31, 2025, the Company's proved undeveloped "
    "reserves decreased as a result of conversions to proved developed reserves, "
    "revisions of previous estimates and the divestiture of non-core assets in "
    "the Midland Basin, which together accounted for the majority of the change.",
)


class TestFindingTables:
    def test_a_reserve_table_becomes_a_site(self, conn):
        with conn:
            _document(conn, 1, "0001-26-1", "fang-20251231.htm")
            _lines(conn, 1, TABLE)
            _reindex(conn)

        hits = reserve_hits(conn)
        assert len(hits) == 1
        site = hits[0].best
        assert site is not None
        assert site.categories == {"developed", "undeveloped", "total"}
        assert (site.first_line, site.last_line) == (100, 106)
        assert site.closable

    def test_prose_about_reserves_is_not_a_table(self, conn):
        # A sentence discussing conversions matches every phrase the index
        # searches on. Counting it as a table sends the parser at a paragraph.
        with conn:
            _document(conn, 1, "0001-26-1", "doc.htm")
            _lines(conn, 1, [PROSE])
            _reindex(conn)

        hits = reserve_hits(conn)
        assert len(hits) == 1
        assert not hits[0].has_table
        assert hits[0].prose_hits == 1

    def test_prose_alongside_a_table_does_not_hide_it(self, conn):
        with conn:
            _document(conn, 1, "0001-26-1", "doc.htm")
            _lines(conn, 1, TABLE + [PROSE])
            _reindex(conn)

        hits = reserve_hits(conn)
        assert hits[0].has_table
        assert hits[0].prose_hits == 1
        assert hits[0].best.label_hits == 3

    def test_a_document_with_no_reserve_language_is_absent(self, conn):
        with conn:
            _document(conn, 1, "0001-26-1", "doc.htm")
            _lines(conn, 1, [(1, "Total revenues"), (2, "Net income per share")])
            _reindex(conn)

        assert reserve_hits(conn) == []


class TestClustering:
    def test_item_2_and_the_supplemental_note_are_separate_sites(self, conn):
        # The same table is printed twice in most 10-Ks, thousands of lines
        # apart. Merging them would report one site spanning the whole filing.
        far = [(n + 3000, text) for n, text in TABLE]
        with conn:
            _document(conn, 1, "0001-26-1", "doc.htm")
            _lines(conn, 1, TABLE + far)
            _reindex(conn)

        sites = reserve_hits(conn)[0].sites
        assert len(sites) == 2
        assert {s.first_line for s in sites} == {100, 3100}

    def test_rows_separated_by_number_lines_stay_one_site(self, conn):
        # Real tables put several figure lines between two row labels. A gap
        # smaller than CLUSTER_GAP is one table.
        rows = [(100, "Proved developed reserves"),
                (100 + CLUSTER_GAP - 1, "Proved undeveloped reserves")]
        with conn:
            _document(conn, 1, "0001-26-1", "doc.htm")
            _lines(conn, 1, rows)
            _reindex(conn)

        assert len(reserve_hits(conn)[0].sites) == 1

    def test_a_long_row_label_is_still_read_as_a_label(self, conn):
        # "Beginning proved undeveloped reserves at December 31, 2024" is 58
        # characters. The threshold has to clear real labels, not just short ones.
        label = "Beginning proved undeveloped reserves at December 31, 2024"
        assert len(label) <= LABEL_MAX_CHARS
        with conn:
            _document(conn, 1, "0001-26-1", "doc.htm")
            _lines(conn, 1, [(100, label)])
            _reindex(conn)

        assert reserve_hits(conn)[0].has_table


class TestRanking:
    def test_a_checkable_table_outranks_a_rollforward_discussion(self, conn):
        # A PUD rollforward names one category many times; a reserve table names
        # three once each. Only the second can have its identity checked, so it
        # ranks first even though it has fewer hits.
        rollforward = [
            (n, "Proved undeveloped reserves") for n in range(500, 520)
        ]
        with conn:
            _document(conn, 1, "0001-26-1", "doc.htm")
            _lines(conn, 1, TABLE + rollforward)
            _reindex(conn)

        sites = reserve_hits(conn)[0].sites
        assert sites[0].categories == {"developed", "undeveloped", "total"}
        assert sites[1].label_hits > sites[0].label_hits

    def test_documents_are_ordered_by_their_best_site(self, conn):
        with conn:
            _document(conn, 1, "0001-26-1", "thin.htm")
            _lines(conn, 1, [(100, "Proved undeveloped reserves")])
            _document(conn, 2, "0001-26-1", "full.htm", kind="exhibit")
            _lines(conn, 2, TABLE)
            _reindex(conn)

        assert [h.name for h in reserve_hits(conn)] == ["full.htm", "thin.htm"]


class TestDocumentSelection:
    def test_an_exhibit_is_reached_when_the_primary_document_is_a_wrapper(self, conn):
        # A 40-F is frequently a cover sheet; the NI 51-101 reserve statements
        # are in the attached Annual Information Form. Reading only the
        # primary document is what missed every Canadian filer.
        with conn:
            _document(conn, 1, "0001-26-9", "cover.htm", form="40-F")
            _lines(conn, 1, [PROSE])
            _document(conn, 2, "0001-26-9", "aif.htm", form="40-F", kind="exhibit")
            _lines(conn, 2, TABLE)
            _reindex(conn)

        chosen = documents_to_parse(conn, "0001-26-9")
        assert [d.name for d in chosen] == ["aif.htm"]
        assert chosen[0].kind == "exhibit"

    def test_prose_only_documents_are_available_but_not_returned_by_default(self, conn):
        # "The tables are somewhere the fetcher has not reached" is a different
        # finding from "this filer discloses nothing", and both need saying.
        with conn:
            _document(conn, 1, "0001-26-9", "cover.htm", form="40-F")
            _lines(conn, 1, [PROSE])
            _reindex(conn)

        assert documents_to_parse(conn, "0001-26-9") == []
        loose = documents_to_parse(conn, "0001-26-9", require_table=False)
        assert [d.name for d in loose] == ["cover.htm"]
        assert loose[0].prose_hits == 1

    def test_filters_narrow_the_index_query(self, conn):
        with conn:
            _document(conn, 1, "0001-26-1", "a.htm", cik="0000000001")
            _lines(conn, 1, TABLE)
            _document(conn, 2, "0002-26-1", "b.htm", cik="0000000002", form="20-F")
            _lines(conn, 2, TABLE)
            _reindex(conn)

        assert len(reserve_hits(conn)) == 2
        assert [h.name for h in reserve_hits(conn, cik="0000000002")] == ["b.htm"]
        assert [h.name for h in reserve_hits(conn, forms=("20-F",))] == ["b.htm"]
        assert [h.name for h in reserve_hits(conn, accession="0001-26-1")] == ["a.htm"]


class TestQuery:
    def test_the_match_expression_is_phrases_not_bare_terms(self, conn):
        # "proved" alone matches every impairment paragraph in the filing.
        assert '"proved developed"' in RESERVE_MATCH
        assert " proved OR" not in RESERVE_MATCH

    def test_the_canadian_forms_are_searched_for(self, conn):
        # NI 51-101 tables say "proved plus probable", never "proved
        # undeveloped". Omitting it loses the 40-F filers entirely.
        assert "proved plus probable" in RESERVE_MATCH
        with conn:
            _document(conn, 1, "0001-26-9", "aif.htm", form="40-F", kind="exhibit")
            _lines(conn, 1, [(100, "Total proved plus probable reserves")])
            _reindex(conn)

        assert reserve_hits(conn)[0].has_table
