"""Read-only web view over the fact store.

Deliberately thin: every query lives in :mod:`basin.store.queries`, so what a
browser renders and what a test asserts on come from the same code. The app
opens the store read-only — a viewer must never be able to mutate the panel.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query, Request
from fastapi.responses import FileResponse, HTMLResponse, Response
from fastapi.staticfiles import StaticFiles

from basin.store import queries
from basin.store.db import DEFAULT_DB_PATH

STATIC_DIR = Path(__file__).parent / "static"

app = FastAPI(title="Basin", docs_url="/api/docs")

_db_path = DEFAULT_DB_PATH


def configure(db_path: Path | str) -> None:
    global _db_path
    _db_path = Path(db_path)


def _conn() -> sqlite3.Connection:
    """Open the store read-only, per request.

    SQLite connections are not shareable across threads, and the URI's
    ``mode=ro`` makes "the dashboard cannot write to the panel" a property of
    the connection rather than a convention.
    """
    if not Path(_db_path).exists():
        raise HTTPException(
            status_code=503,
            detail=f"no fact store at {_db_path}; run scripts/ingest_xbrl.py first",
        )
    conn = sqlite3.connect(f"file:{_db_path}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    return conn


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
def api_summary() -> dict:
    conn = _conn()
    try:
        return queries.summary(conn)
    finally:
        conn.close()


@app.get("/api/concepts")
def api_concepts() -> list[dict]:
    conn = _conn()
    try:
        return queries.concepts(conn)
    finally:
        conn.close()


@app.get("/api/periods")
def api_periods(concept: str | None = None) -> list[str]:
    conn = _conn()
    try:
        return queries.periods(conn, concept)
    finally:
        conn.close()


@app.get("/api/panel")
def api_panel(
    concept: str = Query(...),
    period: str = Query(...),
    product: str | None = None,
) -> list[dict]:
    conn = _conn()
    try:
        return queries.panel(conn, concept, period, product)
    finally:
        conn.close()


@app.get("/api/companies")
def api_companies() -> list[dict]:
    conn = _conn()
    try:
        return queries.companies(conn)
    finally:
        conn.close()


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
    conn = _conn()
    try:
        return queries.trends(conn, concept, normalized=normalized, limit=limit)
    finally:
        conn.close()


@app.get("/api/coverage")
def api_coverage() -> dict:
    conn = _conn()
    try:
        return queries.coverage_matrix(conn)
    finally:
        conn.close()


@app.get("/api/quality")
def api_quality() -> dict:
    conn = _conn()
    try:
        return queries.data_quality(conn)
    finally:
        conn.close()
