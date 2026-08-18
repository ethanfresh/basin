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


# The filing index's document table, one row per attachment.
_ROW_RE = re.compile(r"(?is)<tr[^>]*>(.*?)</tr>")
_CELL_RE = re.compile(r"(?is)<td[^>]*>(.*?)</td>")
_STRIP_TAGS = re.compile(r"<[^>]+>")


def index_documents(client: EdgarClient, cik: str, accession: str) -> list[dict[str, str]]:
    """Every attachment in a filing, with the type EDGAR declares for it.

    Filenames are not a usable signal for what an attachment *is*: one
    earnings release is ``ex_967513.htm``, another ``exh_99.htm``, a third
    ``decresponseannouncementv28.htm``. The filing index states the type
    (``EX-99.1``) in its own column, which is authoritative — the same lesson
    as taking the primary document from ``primaryDocument`` rather than
    guessing at the name.
    """
    import html as html_module

    raw = client.get_text(f"{filing_dir(cik, accession)}/{accession}-index.htm")
    out: list[dict[str, str]] = []
    for row in _ROW_RE.findall(raw):
        cells = [
            html_module.unescape(_STRIP_TAGS.sub("", cell)).replace("\xa0", " ").strip()
            for cell in _CELL_RE.findall(row)
        ]
        # seq, description, document, type, size
        if len(cells) < 4 or not cells[2]:
            continue
        name = cells[2].split()[0]
        out.append({"description": cells[1], "document": name, "type": cells[3]})
    return out


def earnings_exhibits(client: EdgarClient, cik: str, accession: str) -> list[str]:
    """EX-99 attachments, where earnings releases and guidance actually live."""
    return [
        d["document"]
        for d in index_documents(client, cik, accession)
        if d["type"].upper().startswith("EX-99")
        and d["document"].lower().endswith((".htm", ".html"))
    ]
