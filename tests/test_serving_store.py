"""The shipped store has to be the working store minus the corpus, and nothing else."""

from __future__ import annotations

import pytest

from basin.store import connect, connect_readonly, insert_facts, record_filing, upsert_company
from basin.store import queries
from basin.store.serving import EXCLUDED, build, copyable_tables
from basin.facts.xbrl import FactRow


def _fact(**overrides) -> FactRow:
    base = dict(
        cik="0001090012",
        concept_key="proved_developed_reserves_boe",
        taxonomy="srt",
        tag="ProvedDevelopedReservesBOE1",
        value=1200.0,
        unit="MMBoe",
        period_start=None,
        period_end="2024-12-31",
        fiscal_year=2024,
        fiscal_period="FY",
        accession="0001090012-25-000010",
        form="10-K",
        filed="2025-02-20",
    )
    base.update(overrides)
    return FactRow(**base)


@pytest.fixture
def source(tmp_path):
    """A store holding both halves: facts to ship, and a document index not to."""
    path = tmp_path / "working.db"
    conn = connect(path)
    upsert_company(conn, "0001090012", "TEST ENERGY CORP", ticker="TST", cohort="Oil & Gas E&P")
    record_filing(conn, "0001090012-25-000010", "0001090012", "10-K", "2025-02-20")
    insert_facts(conn, [_fact(), _fact(period_end="2023-12-31", fiscal_year=2023)])

    conn.execute(
        "INSERT INTO document (accession, cik, name, form, filed_date, pages, line_count)"
        " VALUES (?, ?, ?, ?, ?, ?, ?)",
        ("0001090012-25-000010", "0001090012", "tst-10k.htm", "10-K", "2025-02-20", 2, 2),
    )
    document_id = conn.execute("SELECT id FROM document").fetchone()[0]
    for line_no, text in enumerate(("Proved developed reserves 1,200", "in MMBoe"), start=1):
        conn.execute(
            "INSERT INTO document_line (document_id, page, line_no, char_offset, text)"
            " VALUES (?, ?, ?, ?, ?)",
            (document_id, 1, line_no, 0, text),
        )
    conn.commit()
    conn.close()
    return path


def test_facts_survive(source, tmp_path):
    counts = build(source, tmp_path / "serving.db")
    assert counts["fact"] == 2
    assert counts["company"] == 1
    assert counts["filing"] == 1


def test_document_index_does_not(source, tmp_path):
    out = tmp_path / "serving.db"
    build(source, out)
    conn = connect_readonly(out)
    # Present and empty, not missing: a query that reaches for them on the host
    # should return no rows rather than raise.
    assert conn.execute("SELECT COUNT(*) FROM document_line").fetchone()[0] == 0
    assert conn.execute("SELECT COUNT(*) FROM document_search").fetchone()[0] == 0
    # The catalogue of what was indexed still ships; verification cites by name.
    assert conn.execute("SELECT COUNT(*) FROM document").fetchone()[0] == 1
    conn.close()


def test_the_dashboard_reads_the_same_panel_from_both(source, tmp_path):
    out = tmp_path / "serving.db"
    build(source, out)
    working, serving = connect_readonly(source), connect_readonly(out)
    for read in (
        lambda c: queries.summary(c),
        lambda c: queries.cohorts(c),
        lambda c: queries.companies(c),
        lambda c: queries.panel_latest(c, "proved_developed_reserves_boe", None, None),
        lambda c: queries.panel_wide(c),
    ):
        assert read(working) == read(serving)
    working.close()
    serving.close()


def test_every_shipped_table_is_copied(source, tmp_path):
    """A table added to schema.sql must be carried without editing this module."""
    out = tmp_path / "serving.db"
    build(source, out)
    conn = connect_readonly(out)
    tables = {
        name
        for (name,) in conn.execute(
            "SELECT name FROM sqlite_schema WHERE type = 'table'"
        )
        if not name.startswith("sqlite_")
    }
    conn.close()
    # Everything in the schema is either copied or deliberately left behind.
    left_behind = tables - set(copyable_tables(connect(out)))
    assert left_behind == EXCLUDED | {t for t in tables if t.startswith("document_search_")}


def test_a_broken_source_does_not_ship(source, tmp_path):
    """A fact whose filing went missing is a citation that cannot be checked."""
    conn = connect(source)
    conn.execute("PRAGMA foreign_keys = OFF")
    conn.execute("DELETE FROM filing")
    conn.commit()
    conn.close()
    with pytest.raises(ValueError, match="foreign key"):
        build(source, tmp_path / "serving.db")
