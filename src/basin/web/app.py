"""Read-only web view over the fact store.

Deliberately thin: every query lives in :mod:`basin.store.queries`, so what a
browser renders and what a test asserts on come from the same code. The app
opens the store read-only — a viewer must never be able to mutate the panel.
"""

from __future__ import annotations

import sqlite3
import threading
import time
from contextlib import asynccontextmanager
from pathlib import Path
from typing import Any, Callable

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from basin.store import queries
from basin.store.db import DEFAULT_DB_PATH, connect_readonly

STATIC_DIR = Path(__file__).parent / "static"

@asynccontextmanager
async def _lifespan(_: FastAPI):
    """Warm the cache in the background, so the port opens immediately."""
    threading.Thread(target=warm, name="basin-warm", daemon=True).start()
    yield


app = FastAPI(title="Basin", docs_url="/api/docs", lifespan=_lifespan)

_db_path = DEFAULT_DB_PATH


def configure(db_path: Path | str) -> None:
    global _db_path
    _db_path = Path(db_path)
    clear_cache()


def _read(query: Callable[..., Any], *args: Any) -> Any:
    """Run one query against the store and close the connection."""
    conn = _conn()
    try:
        return query(conn, *args)
    finally:
        conn.close()


def _conn() -> sqlite3.Connection:
    """Open the store read-only, per request.

    SQLite connections are not shareable across threads, so this is per-request
    rather than a module-level handle. The read-only guarantee lives in
    :func:`basin.store.db.connect_readonly`, shared with any other reader.

    The existence check is what turns a missing store into a 503 that names the
    path, rather than the bare "unable to open database file" SQLite raises --
    on a deploy whose volume did not mount, that distinction is the whole
    diagnosis.
    """
    if not Path(_db_path).exists():
        raise HTTPException(
            status_code=503,
            detail=f"no fact store at {_db_path}; run scripts/ingest_xbrl.py first",
        )
    return connect_readonly(_db_path)


# Read answers are cached against the store's own timestamp.
#
# Every panel query ends in a window function over the whole fact table, and
# several of them nest a second one, so a page load spent about a second
# recomputing rankings that had not changed -- and each cohort click spent it
# again. The store is written by ingest passes, not by the viewer, so the file
# itself says when an answer could have changed: cache on (mtime, size) and a
# stale entry is impossible without a write.
#
# Keyed on the path too, so a test that points the app at a second store cannot
# be served the first one's answers.
_cache: dict[tuple, Any] = {}
_cache_stamp: tuple | None = None
_cache_checked = 0.0
_cache_lock = threading.Lock()

# How long an answer may go unquestioned. The store is checked at most this
# often, so a figure can be this far behind a write and no further.
#
# It is not a performance knob, it is what makes the cache survive an ingest.
# Checking on every request sounds stricter and is worse: a verification or
# extraction pass touches the store every second or two for an hour, so each
# request found a new timestamp, cleared everything, and the viewer paid the
# full cost of every query for the length of the run -- exactly when someone is
# most likely to be watching the numbers move.
_STALE_AFTER = 2.0


def _stamp() -> tuple:
    """What the store looked like, cheaply enough to check per request."""
    try:
        st = Path(_db_path).stat()
        return (str(_db_path), st.st_mtime_ns, st.st_size)
    except OSError:
        return (str(_db_path), None, None)


def cached(key: tuple, compute: Callable[[], Any]) -> Any:
    """*compute*'s result, recomputed only when the store has changed.

    Two callers racing on a cold cache both compute, which costs a duplicated
    query and no correctness: the value is a pure function of the store they
    both stamped. Holding the lock across the query instead would serialise
    every reader behind the slowest one.
    """
    global _cache_stamp, _cache_checked
    with _cache_lock:
        now = time.monotonic()
        if _cache_stamp is None or now - _cache_checked >= _STALE_AFTER:
            _cache_checked = now
            stamp = _stamp()
            if stamp != _cache_stamp:
                _cache.clear()
                _cache_stamp = stamp
        if key in _cache:
            return _cache[key]
        computed_for = _cache_stamp

    value = compute()

    with _cache_lock:
        # Dropped rather than stored if the store moved underneath the query.
        if _cache_stamp == computed_for:
            _cache[key] = value
    return value


def warm(cohorts: list[str | None] | None = None) -> None:
    """Answer the panel's opening questions before anyone asks them.

    The first visitor after a restart otherwise pays for every query in the
    boot sequence at once. Nothing here is required for correctness -- a cold
    cache answers the same, just slower -- so every failure is swallowed: a
    store that is missing, locked or mid-write is a reason to serve the page
    uncached, not a reason to refuse to start.
    """
    try:
        if cohorts is None:
            cohorts = [None] + [r["cohort"] for r in api_cohorts()]
        api_concepts()
        api_periods()
        for cohort in cohorts:
            api_summary(cohort)
            api_panel_wide(None, None, cohort)
    except Exception:  # noqa: BLE001 -- see the docstring
        pass


def clear_cache() -> None:
    """Drop every cached answer. For tests, and for a store swapped in place."""
    global _cache_stamp, _cache_checked
    with _cache_lock:
        _cache.clear()
        _cache_stamp = None
        _cache_checked = 0.0


app.mount("/static", StaticFiles(directory=STATIC_DIR), name="static")


@app.get("/", response_class=HTMLResponse)
@app.head("/")  # supervisors probe with HEAD; a bare @get answers 405
def index(request: Request) -> HTMLResponse:
    """Serve the page with its social-card URLs made absolute.

    Open Graph requires absolute URLs -- a relative og:image is simply
    dropped by every scraper -- and the host is only known per request, so
    the base is substituted here rather than hardcoded into the file.
    """
    html = (STATIC_DIR / "index.html").read_text()
    return HTMLResponse(html.replace("{{BASE_URL}}", str(request.base_url).rstrip("/")))


@app.get("/favicon.ico")
def favicon() -> FileResponse:
    return FileResponse(STATIC_DIR / "favicon-32.png", media_type="image/png")


@app.get("/debug/page/{accession}/{document}/{sheet}")
def debug_page(accession: str, document: str, sheet: int) -> Response:
    """One sheet of a stored filing, rendered as the filer wrote it.

    A visual check on the coordinate system: what the parser calls sheet N
    should look like one printed page, with the figures where the locators
    say they are. Debug-only — reads the corpus, not the store.
    """
    from basin.documents import corpus
    from basin.documents.text import _PAGE_BREAK

    raw = corpus.load_raw(accession, document)
    if raw is None:
        raise HTTPException(status_code=404, detail="not in corpus")
    bounds, previous = [], 0
    for match in _PAGE_BREAK.finditer(raw):
        bounds.append((previous, match.start()))
        previous = match.end()
    bounds.append((previous, len(raw)))
    if not 1 <= sheet <= len(bounds):
        raise HTTPException(status_code=404, detail=f"sheet out of range 1..{len(bounds)}")
    start, end = bounds[sheet - 1]
    return Response(
        content=(
            "<html><head><meta charset='utf-8'><style>"
            "body{background:#fff;color:#000;padding:24px;max-width:1000px;margin:auto}"
            "</style></head><body>" + raw[start:end] + "</body></html>"
        ),
        media_type="text/html",
    )


@app.get("/api/summary")
def api_summary(cohort: str | None = None) -> dict:
    return cached(("summary", cohort), lambda: _read(queries.summary, cohort))


@app.get("/api/concepts")
def api_concepts() -> list[dict]:
    return cached(("concepts",), lambda: _read(queries.concepts))


@app.get("/api/periods")
def api_periods(concept: str | None = None) -> list[str]:
    return cached(("periods", concept), lambda: _read(queries.periods, concept))


@app.get("/api/panel")
def api_panel(
    concept: str = Query(...),
    period: str = Query(...),
    product: str | None = None,
    cohort: str | None = None,
) -> list[dict]:
    def read(conn):
        # "latest" is not a period, it is a mode: each company's own most
        # recent. Fiscal years do not align and filers stop tagging, so a
        # single period_end understates coverage by about a fifth.
        if period == queries.LATEST_PERIOD:
            return queries.panel_latest(conn, concept, product, cohort)
        return queries.panel(conn, concept, period, product, cohort)

    return cached(("panel", concept, period, product, cohort),
                  lambda: _read(read))


@app.get("/api/panel-wide")
def api_panel_wide(
    period: str | None = None,
    product: str | None = None,
    cohort: str | None = None,
) -> dict:
    # "latest" is a mode, not a period: each company's own most recent value
    # for each concept. Passing None to the query means the same thing.
    at = None if period in (None, "", queries.LATEST_PERIOD) else period
    return cached(
        ("panel-wide", at, product, cohort),
        lambda: _read(queries.panel_wide, at, product, cohort),
    )


@app.get("/api/cohorts")
def api_cohorts() -> list[dict]:
    return cached(("cohorts",), lambda: _read(queries.cohorts))


@app.get("/api/companies")
def api_companies() -> list[dict]:
    return cached(("companies",), lambda: _read(queries.companies))


@app.get("/api/company/{cik}")
def api_company(cik: str) -> list[dict]:
    conn = _conn()
    try:
        return queries.company_series(conn, cik)
    finally:
        conn.close()


@app.get("/api/fact/{fact_id}/locator")
def api_fact_locator(fact_id: int) -> dict:
    conn = _conn()
    try:
        found = queries.fact_locator(conn, fact_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"no fact {fact_id}")
        return found
    finally:
        conn.close()


@app.get("/api/citation/{fact_id}")
def api_citation(fact_id: int) -> dict:
    conn = _conn()
    try:
        found = queries.citation(conn, fact_id)
        if found is None:
            raise HTTPException(status_code=404, detail=f"no fact {fact_id}")
        return found
    finally:
        conn.close()


@app.get("/api/trends")
def api_trends(concept: str = Query(...), normalized: bool = True, limit: int = 12) -> dict:
    return cached(
        ("trends", concept, normalized, limit),
        lambda: _read(lambda conn: queries.trends(
            conn, concept, normalized=normalized, limit=limit)),
    )


@app.get("/api/kpis")
def api_kpis(cohort: str | None = None) -> dict:
    return cached(("kpis", cohort),
                  lambda: _read(queries.company_concepts, cohort))


@app.get("/api/history")
def api_history(cik: str = Query(...), concept: str = Query(...),
                product: str | None = None) -> dict:
    conn = _conn()
    try:
        return queries.company_history(conn, cik, concept, product)
    finally:
        conn.close()


@app.get("/api/coverage")
def api_coverage(cohort: str | None = None) -> dict:
    return cached(("coverage", cohort),
                  lambda: _read(queries.coverage_matrix, cohort))


@app.get("/api/quality")
def api_quality() -> dict:
    return cached(("quality",), lambda: _read(queries.data_quality))
