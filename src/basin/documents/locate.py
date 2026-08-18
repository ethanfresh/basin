"""Find the human-readable document behind an accession number."""

from __future__ import annotations

import json
import re
from pathlib import Path

from basin.edgar.client import SEC_WWW_HOST, EdgarClient, cik_padded

SUBMISSIONS_CACHE = Path("data/cache/submissions")

_EXHIBIT = re.compile(r"(?i)ex-?\d")


def filing_dir(cik: str, accession: str) -> str:
    return f"{SEC_WWW_HOST}/Archives/edgar/data/{int(cik)}/{accession.replace('-', '')}"


def primary_document(
    cik: str, accession: str, *, client: EdgarClient | None = None
) -> str | None:
    """Filename of the filing's main document.

    Prefers the ``primaryDocument`` the submissions API records, because it is
    the filer's own answer. Filenames are no guide: Diamondback's 10-K is
    ``fang-20251231.htm``, which contains neither "10" nor "k".
    """
    cached = SUBMISSIONS_CACHE / f"CIK{cik_padded(cik)}.json"
    if cached.exists():
        recent = json.loads(cached.read_text()).get("filings", {}).get("recent", {})
        accessions = recent.get("accessionNumber", [])
        if accession in accessions:
            doc = recent.get("primaryDocument", [])[accessions.index(accession)]
            if doc:
                return doc

    if client is None:
        return None

    # Fall back to the filing's own index: the largest non-exhibit HTML file.
    index = client.get_json(f"{filing_dir(cik, accession)}/index.json")
    best, best_size = None, -1
    for item in index.get("directory", {}).get("item", []):
        name = item.get("name", "")
        if not name.endswith((".htm", ".html")) or _EXHIBIT.search(name):
            continue
        if "index" in name:
            continue
        size = int(item.get("size") or 0)
        if size > best_size:
            best, best_size = name, size
    return best


def document_url(cik: str, accession: str, document: str) -> str:
    return f"{filing_dir(cik, accession)}/{document}"
