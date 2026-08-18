"""Read-only web view over the fact store.

Deliberately thin: every query lives in :mod:`basin.store.queries`, so what a
browser renders and what a test asserts on come from the same code. The app
opens the store read-only — a viewer must never be able to mutate the panel.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from fastapi import FastAPI, HTTPException, Query
from fastapi.responses import FileResponse, Response

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


@app.get("/")
@app.head("/")  # supervisors probe with HEAD; a bare @get answers 405
def index() -> FileResponse:
    return FileResponse(STATIC_DIR / "index.html")


@app.get("/favicon.ico")
def favicon() -> Response:
    dot = (
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32">'
        '<rect width="32" height="32" fill="#000"/>'
        '<circle cx="16" cy="16" r="7" fill="#00e585"/></svg>'
    )
    return Response(content=dot, media_type="image/svg+xml")


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
