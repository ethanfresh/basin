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
        out.append({
            "description": cells[1],
            "document": name,
            "type": cells[3],
            "size": _int(cells[4]) if len(cells) > 4 else 0,
        })
    return out


def _int(cell: str) -> int:
    try:
        return int(re.sub(r"[^0-9]", "", cell) or 0)
    except ValueError:
        return 0


def earnings_exhibits(client: EdgarClient, cik: str, accession: str) -> list[str]:
    """EX-99 attachments, where earnings releases and guidance actually live."""
    return [
        d["document"]
        for d in index_documents(client, cik, accession)
        if d["type"].upper().startswith("EX-99")
        and d["document"].lower().endswith((".htm", ".html"))
    ]


# A certification or an auditor's consent is a page or two; an Annual
# Information Form runs to hundreds of kilobytes. Nothing else in the filing
# index separates them -- most filers write "EX-99.1" in the description column
# rather than describing anything -- so size is the only usable signal, and it
# is read from the index rather than guessed from the filename.
MIN_SUBSTANTIVE_EXHIBIT_BYTES = 25_000


def annual_report_exhibits(
    client: EdgarClient, cik: str, accession: str,
    *, min_bytes: int = MIN_SUBSTANTIVE_EXHIBIT_BYTES,
) -> list[str]:
    """The substantive attachments of a 40-F.

    A 40-F is frequently a cover sheet. Cenovus's primary document is 15KB and
    says almost nothing; the Annual Information Form carrying its NI 51-101
    reserve statements is EX-99.1, and the reserve report is another exhibit
    again. Fetching ``primaryDocument`` alone finds no reserves and reads as a
    filer that discloses none -- the same trap as 8-K EX-99.1, in a new place.

    Which exhibit holds the reserves is not knowable from the index. Baytex
    files its AIF as EX-99.1 and an ASC 932 supplement as EX-99.4; Canadian
    Natural puts the reserve discussion in the 40-F itself and files financial
    statements as EX-99.1. So everything substantial is taken, and deciding
    what is in each one is left to the parser -- fetching is the rate-limited
    step and happens once, while parsing happens on every read.

    Certifications and consents are excluded by size. They are numerous, they
    are always small, and none of them ever carries a reserve disclosure.
    """
    return [
        d["document"]
        for d in index_documents(client, cik, accession)
        if d["type"].upper().startswith("EX-99")
        and d["document"].lower().endswith((".htm", ".html"))
        and d["size"] >= min_bytes
    ]


# Forms whose primary document is a cover sheet. What the filing actually
# reports is attached: a 40-F wraps the Annual Information Form and financial
# statements, a 6-K wraps a foreign issuer's interim results, an 8-K wraps the
# earnings release. Verifying against the primary document alone finds nothing
# and records "not found", which reads as a value the filer never printed.
WRAPPER_FORMS = ("40-F", "6-K", "8-K")


def is_wrapper_form(form: str | None) -> bool:
    return bool(form) and form.split("/")[0] in WRAPPER_FORMS


def substantive_exhibits(
    client: EdgarClient, cik: str, accession: str,
    *, limit: int = 6, min_bytes: int = 4_000,
) -> list[str]:
    """EX-99 attachments of a filing, largest first.

    A lower size floor than :func:`annual_report_exhibits` on purpose. That one
    is picking the Annual Information Form out of a pile of certifications and
    can afford to be strict; this one is trying to find a single figure that
    could be anywhere, and a 6-K's interim results release is often only a few
    kilobytes. Certifications are still excluded -- they are smaller again --
    and the count is capped so a filing with twenty attachments cannot turn one
    verification into twenty fetches.
    """
    documents = [
        d for d in index_documents(client, cik, accession)
        if d["type"].upper().startswith("EX-99")
        and d["document"].lower().endswith((".htm", ".html"))
        and d["size"] >= min_bytes
    ]
    documents.sort(key=lambda d: d["size"], reverse=True)
    return [d["document"] for d in documents[:limit]]
