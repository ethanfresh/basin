"""The viewer's answer cache: fast, and never stale past its window."""

from __future__ import annotations

import sys

import pytest

import basin.web.app  # noqa: F401  (imported for its side effect on sys.modules)
from basin.facts.xbrl import FactRow
from basin.store import connect, insert_facts, record_filing, upsert_company

# `basin.web` exports the FastAPI instance as `app`, so `basin.web.app` reads
# as that object rather than as the module holding the cache.
web = sys.modules["basin.web.app"]


def _fact(value: float) -> FactRow:
    return FactRow(
        cik="0001090012", concept_key="proved_reserves_boe", taxonomy="srt",
        tag="ProvedDevelopedAndUndevelopedReservesNet", value=value, unit="MMBoe",
        period_start=None, period_end="2024-12-31", fiscal_year=2024,
        fiscal_period="FY", accession="0001090012-25-000010", form="10-K",
        filed="2025-02-20",
    )


@pytest.fixture
def store(tmp_path):
    path = tmp_path / "cache.db"
    conn = connect(path)
    upsert_company(conn, "0001090012", "Devon Energy Corp", ticker="DVN",
                   cohort="Oil & Gas E&P")
    record_filing(conn, "0001090012-25-000010", "0001090012", "10-K", "2025-02-20")
    insert_facts(conn, [_fact(1200.0)])
    conn.commit()
    conn.close()
    web.configure(path)
    yield path
    web.clear_cache()


class TestAnswerCache:
    def test_a_repeated_question_is_not_asked_twice(self, store):
        calls = []
        for _ in range(3):
            web.cached(("k",), lambda: calls.append(1) or "answer")
        assert len(calls) == 1

    def test_a_different_key_is_a_different_answer(self, store):
        assert web.cached(("a",), lambda: 1) == 1
        assert web.cached(("b",), lambda: 2) == 2

    def test_a_write_invalidates_once_the_window_has_passed(self, store, monkeypatch):
        monkeypatch.setattr(web, "_STALE_AFTER", 0.0)
        assert web.cached(("summary",), lambda: web._read(web.queries.summary))["facts"] == 1

        conn = connect(store)
        insert_facts(conn, [_fact(1300.0)])
        record_filing(conn, "0001090012-26-000010", "0001090012", "10-K", "2026-02-20")
        insert_facts(conn, [FactRow(**{**_fact(1300.0).__dict__,
                                       "accession": "0001090012-26-000010",
                                       "period_end": "2025-12-31"})])
        conn.commit()
        conn.close()

        assert web.cached(("summary",), lambda: web._read(web.queries.summary))["facts"] == 2

    def test_the_store_is_not_statted_on_every_request(self, store, monkeypatch):
        # The point of the window: an ingest writing every second would
        # otherwise clear the cache on every request for the length of its run.
        stats = []
        real = web._stamp
        monkeypatch.setattr(web, "_stamp", lambda: stats.append(1) or real())
        for _ in range(5):
            web.cached(("k",), lambda: "answer")
        assert len(stats) == 1

    def test_configure_forgets_the_previous_store(self, store, tmp_path):
        web.cached(("k",), lambda: "first store")
        other = tmp_path / "other.db"
        connect(other).close()
        web.configure(other)
        assert web.cached(("k",), lambda: "second store") == "second store"
