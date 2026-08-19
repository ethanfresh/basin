"""Following a ticker across a change of registrant.

A company can replace the legal entity that files with the SEC -- a
redomiciliation, a holding-company reorganisation, a merger -- without changing
what the business is or what ticker it trades under. EDGAR assigns the new
entity its own CIK and its own file number, and the SEC's ticker map points the
symbol at it immediately. The filing history does not move.

The result is a ticker that resolves to an empty registrant. XOM resolves to
ExxonMobil Holdings Corp (CIK 2115436), which has filed 10-Qs and nothing else,
while every 10-K sits on Exxon Mobil Corp (CIK 34088). Reading the new CIK does
not fail -- it returns nothing, which is worse, because nothing looks like a
filer that simply does not tag its reserves.

The link is established from evidence rather than by matching names:

  * Rule 12g-3(a) requires the successor to register on **Form 8-K12B**, so the
    presence of that form is what identifies a succession at all.
  * That filing names the predecessor registrant in its explanatory note.
  * The name is confirmed against EDGAR's company search restricted to filers
    that have actually submitted a 10-K, which is the history being looked for.

A succession is only recorded when all three agree. A name that cannot be
confirmed is reported unresolved, never guessed at.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from basin.edgar.client import SEC_WWW_HOST, EdgarClient, NotFound, cik_padded
from basin.edgar.discovery import submissions_url

SUCCESSION_FORM = "8-K12B"

# "Exxon Mobil Corporation, a New Jersey corporation and the predecessor
# registrant". The name is what precedes the phrase, on the same sentence.
_PREDECESSOR_RE = re.compile(
    r"([A-Z][A-Za-z0-9&.,'\- ]{3,80}?)\s*,\s*an?\s+[A-Za-z ]+?\s+"
    r"(?:corporation|company|limited liability company)\s+and\s+the\s+predecessor\s+registrant",
    re.IGNORECASE,
)
# Fallback: "... the predecessor registrant, Foo Corporation".
_PREDECESSOR_RE_ALT = re.compile(
    r"predecessor\s+registrant[,\s]+([A-Z][A-Za-z0-9&.,'\- ]{3,80}?)\s*[,.(]",
)

# "the Corporation became the successor issuer to the Trust under the Exchange
# Act". Common in the 2011-era Canadian trust conversions, and it usually yields
# a defined term ("the Trust") rather than a name -- which the confirmation step
# then declines to resolve, leaving the row unconfirmed rather than wrong.
_PREDECESSOR_RE_SUCCESSOR_TO = re.compile(
    r"successor\s+issuer\s+to\s+(?:the\s+)?([A-Z][A-Za-z0-9&.,'\- ]{2,60}?)\s+"
    r"(?:under|pursuant|,)",
)

_CIK_RE = re.compile(r"<cik>(\d+)</cik>")
_NAME_RE = re.compile(r"<conformed-name>(.*?)</conformed-name>", re.IGNORECASE)


@dataclass(frozen=True)
class Succession:
    """One registrant superseding another, with the filing that says so."""

    successor_cik: str
    successor_name: str
    predecessor_cik: str | None
    predecessor_name: str | None
    accession: str | None
    filed_date: str | None
    status: str
    """``resolved``, ``unconfirmed`` (named but not found), or ``none``."""

    note: str | None = None


def find_succession(client: EdgarClient, cik: int | str) -> Succession:
    """Resolve the registrant that *cik* succeeded, if it succeeded one."""
    padded = cik_padded(cik)
    try:
        payload = client.get_json(submissions_url(padded))
    except NotFound:
        return Succession(padded, "", None, None, None, None, "none",
                          "no submissions record")

    name = payload.get("name", "")
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    index = next((i for i, f in enumerate(forms) if f == SUCCESSION_FORM), None)
    if index is None:
        return Succession(padded, name, None, None, None, None, "none",
                          f"no {SUCCESSION_FORM} filed")

    accession = recent.get("accessionNumber", [])[index]
    filed = recent.get("filingDate", [])[index]
    document = recent.get("primaryDocument", [])[index]

    text = _filing_text(client, padded, accession, document)
    predecessor_name = extract_predecessor_name(text)
    if not predecessor_name:
        return Succession(padded, name, None, None, accession, filed,
                          "unconfirmed", f"{SUCCESSION_FORM} names no predecessor")

    resolved = resolve_company_name(client, predecessor_name)
    if not resolved:
        return Succession(padded, name, None, predecessor_name, accession, filed,
                          "unconfirmed", "predecessor has no 10-K filer on EDGAR")

    predecessor_cik, conformed = resolved
    if predecessor_cik == padded:
        return Succession(padded, name, None, predecessor_name, accession, filed,
                          "unconfirmed", "resolved to itself")

    return Succession(padded, name, predecessor_cik, conformed, accession, filed,
                      "resolved")


def extract_predecessor_name(text: str) -> str | None:
    """Pull the predecessor registrant's name out of an 8-K12B."""
    for pattern in (_PREDECESSOR_RE, _PREDECESSOR_RE_ALT,
                    _PREDECESSOR_RE_SUCCESSOR_TO):
        match = pattern.search(text)
        if match:
            name = re.sub(r"\s+", " ", match.group(1)).strip(" ,.")
            # The regex can run back into the preceding clause; keep the tail
            # after a date or a comma-led preamble.
            name = re.split(r"(?:^|\s)(?:On\s+\w+\s+\d+,\s+\d{4},?\s*)", name)[-1]
            return name.strip() or None
    return None


def resolve_company_name(
    client: EdgarClient, name: str, *, form_type: str = "10-K"
) -> tuple[str, str] | None:
    """Find the CIK of the filer called *name* that has submitted *form_type*.

    Restricting to 10-K filers is what makes this safe: the successor itself has
    none, so it cannot be returned, and the search is looking for exactly the
    history that the successor lacks. Only an unambiguous single hit is taken.
    """
    query = re.sub(r"[^A-Za-z0-9 ]", " ", name)
    query = re.sub(r"\b(inc|corp|corporation|company|co|plc|ltd|limited|lp|llc|holdings|sa|nv)\b",
                   " ", query, flags=re.IGNORECASE)
    query = re.sub(r"\s+", " ", query).strip()
    if not query:
        return None

    url = (
        f"{SEC_WWW_HOST}/cgi-bin/browse-edgar?action=getcompany"
        f"&company={query.replace(' ', '+')}&type={form_type}"
        f"&dateb=&owner=include&count=40&output=atom"
    )
    body = client.get_text(url)
    ciks = _CIK_RE.findall(body)
    names = _NAME_RE.findall(body)
    if len(set(ciks)) != 1:
        return None
    return cik_padded(ciks[0]), (names[0].strip() if names else name)


def _filing_text(client: EdgarClient, cik: str, accession: str, document: str) -> str:
    """Fetch a filing's primary document and flatten it to searchable text."""
    stripped = accession.replace("-", "")
    url = (
        f"{SEC_WWW_HOST}/Archives/edgar/data/{int(cik)}/{stripped}/{document}"
    )
    html = client.get_text(url)
    text = re.sub(r"<[^>]+>", " ", html)
    text = re.sub(r"&#\d+;|&nbsp;|&amp;", " ", text)
    return re.sub(r"\s+", " ", text)
