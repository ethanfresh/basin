"""Enumerate SIC-coded filers and describe them from the submissions API.

Cohort selection is a decision that has to be defensible, so it is driven by
measured facts about each filer — when it last filed a 10-K, whether it files
8-Ks, where it is domiciled — rather than by a hand-written list.

Two endpoints:

    /cgi-bin/browse-edgar?action=getcompany&SIC=####&type=10-K&output=atom
        Paginated company search. Returns CIKs. NOTE: its ``name`` fields come
        back as ``ARRAY(0x...)`` — a long-standing SEC serialisation bug — so
        names must come from elsewhere.

    https://data.sec.gov/submissions/CIK##########.json
        Everything else: name, tickers, SIC description, address, filing index.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from basin.edgar.client import SEC_DATA_HOST, SEC_WWW_HOST, EdgarClient, NotFound, cik_padded

_CIK_RE = re.compile(r"<cik>(\d+)</cik>")

BROWSE_PAGE_SIZE = 100


def sic_ciks(client: EdgarClient, sic: str | int, *, form_type: str = "10-K") -> list[str]:
    """Every CIK under *sic* that has filed *form_type*, in EDGAR's order.

    Paginates until a page returns no CIK it has not already seen. EDGAR clamps
    ``start`` past the end of the result set by repeating the final page rather
    than returning an empty one, so "no new CIKs" is the reliable terminator.
    """
    seen: dict[str, None] = {}
    start = 0

    while True:
        url = (
            f"{SEC_WWW_HOST}/cgi-bin/browse-edgar?action=getcompany&SIC={sic}"
            f"&type={form_type}&dateb=&owner=include&count={BROWSE_PAGE_SIZE}"
            f"&start={start}&output=atom"
        )
        page = _CIK_RE.findall(client.get_text(url))
        fresh = [cik_padded(c) for c in page if cik_padded(c) not in seen]
        if not fresh:
            return list(seen)
        for cik in fresh:
            seen[cik] = None
        start += BROWSE_PAGE_SIZE


@dataclass(frozen=True)
class FilerProfile:
    """What the submissions API knows about one filer."""

    cik: str
    name: str
    tickers: tuple[str, ...]
    exchanges: tuple[str, ...]
    sic: str
    sic_description: str
    state_or_country: str
    """EDGAR's business-address code: a US state ("TX") or a foreign code ("A0")."""

    state_or_country_description: str
    """EDGAR echoes the code itself for US states, and names foreign places."""

    latest_10k_date: str | None
    latest_10k_accession: str | None
    tenk_count: int
    eightk_count: int
    latest_8k_date: str | None
    former_names: tuple[str, ...] = ()
    error: str | None = None

    @property
    def is_foreign(self) -> bool:
        """True when the business address is outside the US.

        EDGAR never writes "US". It puts the bare state code in both fields for
        a domestic filer ("TX" / "TX") and gives foreign codes a real
        description ("A0" / "Alberta, Canada"), so the two fields differing is
        the signal. An unknown address is not treated as foreign.
        """
        if not self.state_or_country:
            return False
        return self.state_or_country_description != self.state_or_country

    def filed_10k_since(self, date: str) -> bool:
        return bool(self.latest_10k_date and self.latest_10k_date >= date)


def submissions_url(cik: int | str) -> str:
    return f"{SEC_DATA_HOST}/submissions/CIK{cik_padded(cik)}.json"


def fetch_profile(client: EdgarClient, cik: int | str) -> FilerProfile:
    """Fetch and summarise one filer's submissions record."""
    padded = cik_padded(cik)
    try:
        payload = client.get_json(submissions_url(padded))
    except NotFound:
        return _empty_profile(padded, "no submissions record (404)")

    return profile_from_submissions(payload)


def profile_from_submissions(payload: dict) -> FilerProfile:
    """Summarise a submissions payload. Pure — no network, so it is testable.

    Only the ``recent`` filing block is read. It covers roughly the last
    thousand filings, which is far more than enough to answer "has this filer
    submitted a 10-K lately"; the older paginated blocks are not fetched
    because nothing in cohort selection depends on deep history.
    """
    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])
    accessions = recent.get("accessionNumber", [])

    tenk_dates: list[tuple[str, str]] = []
    eightk_dates: list[str] = []
    for i, form in enumerate(forms):
        date = dates[i] if i < len(dates) else ""
        # Match 10-K and 10-K/A but not 10-KT or 10-Q.
        if form == "10-K" or form.startswith("10-K/"):
            tenk_dates.append((date, accessions[i] if i < len(accessions) else ""))
        elif form == "8-K" or form.startswith("8-K/"):
            eightk_dates.append(date)

    tenk_dates.sort()
    eightk_dates.sort()

    addresses = payload.get("addresses", {}) or {}
    business = addresses.get("business", {}) or {}

    return FilerProfile(
        cik=cik_padded(payload.get("cik", 0)),
        name=payload.get("name", ""),
        # Both lists can contain nulls: EDGAR pads `exchanges` to the length of
        # `tickers`, and a ticker with no listed exchange leaves a None behind.
        tickers=tuple(t for t in (payload.get("tickers") or ()) if t),
        exchanges=tuple(e for e in (payload.get("exchanges") or ()) if e),
        sic=str(payload.get("sic", "")),
        sic_description=payload.get("sicDescription", ""),
        state_or_country=business.get("stateOrCountry", "") or "",
        state_or_country_description=business.get("stateOrCountryDescription", "") or "",
        latest_10k_date=tenk_dates[-1][0] if tenk_dates else None,
        latest_10k_accession=tenk_dates[-1][1] if tenk_dates else None,
        tenk_count=len(tenk_dates),
        eightk_count=len(eightk_dates),
        latest_8k_date=eightk_dates[-1] if eightk_dates else None,
        former_names=tuple(
            fn.get("name", "") for fn in (payload.get("formerNames") or [])
        ),
    )


def _normalise_name(name: str) -> str:
    """Strip punctuation and corporate suffixes so name matching is stable."""
    cleaned = re.sub(r"[^a-z0-9 ]", " ", name.lower())
    words = [
        w
        for w in cleaned.split()
        if w not in {"inc", "corp", "corporation", "co", "company", "llc", "lp", "ltd", "plc", "the", "new", "de"}
    ]
    return " ".join(words)


@dataclass(frozen=True)
class IssuerGroup:
    """One real-world issuer, and every CIK EDGAR files it under."""

    primary: FilerProfile
    """The CIK that is currently filing — the one the cohort should use."""

    superseded: tuple[FilerProfile, ...]
    reason: str

    @property
    def all_ciks(self) -> tuple[str, ...]:
        return (self.primary.cik,) + tuple(p.cik for p in self.superseded)


def dedupe_issuers(profiles: list[FilerProfile]) -> list[IssuerGroup]:
    """Collapse CIKs that belong to the same issuer.

    A reorganisation gives a company a new CIK while the old one keeps its
    filing history, so a naive count double-counts the issuer and a naive
    cohort could hold the same company twice. APA Corporation (CIK 1841666)
    and Apache Corporation (CIK 6769) are the same business.

    Two signals, both conservative:

      * a shared ticker -- two CIKs cannot list the same ticker unless they
        are the same issuer or one has succeeded the other
      * one CIK's current name matching another's *former* name, which is what
        a rename or reorganisation leaves behind in EDGAR

    Grouping only ever merges; where the evidence is absent the CIKs stay
    separate, because wrongly merging two real companies silently drops one
    from the cohort.
    """
    parent: dict[str, str] = {p.cik: p.cik for p in profiles}

    def find(cik: str) -> str:
        while parent[cik] != cik:
            parent[cik] = parent[parent[cik]]
            cik = parent[cik]
        return cik

    def union(a: str, b: str) -> None:
        ra, rb = find(a), find(b)
        if ra != rb:
            parent[rb] = ra

    by_ticker: dict[str, list[str]] = {}
    by_name: dict[str, list[str]] = {}
    reasons: dict[str, str] = {}

    for p in profiles:
        for ticker in p.tickers:
            by_ticker.setdefault(ticker.upper(), []).append(p.cik)
        key = _normalise_name(p.name)
        if key:
            by_name.setdefault(key, []).append(p.cik)

    for ticker, ciks in by_ticker.items():
        for other in ciks[1:]:
            union(ciks[0], other)
            reasons[find(ciks[0])] = f"shared ticker {ticker}"

    for p in profiles:
        for former in p.former_names:
            key = _normalise_name(former)
            for other in by_name.get(key, []):
                if other != p.cik:
                    union(p.cik, other)
                    reasons[find(p.cik)] = f"former name “{former}”"

    grouped: dict[str, list[FilerProfile]] = {}
    for p in profiles:
        grouped.setdefault(find(p.cik), []).append(p)

    groups: list[IssuerGroup] = []
    for root, members in grouped.items():
        # The issuer that is still filing is the one the cohort should track.
        members.sort(
            key=lambda p: (p.latest_10k_date or "", p.tenk_count), reverse=True
        )
        groups.append(
            IssuerGroup(
                primary=members[0],
                superseded=tuple(members[1:]),
                reason=reasons.get(root, "") if len(members) > 1 else "",
            )
        )

    groups.sort(key=lambda g: g.primary.name)
    return groups


def _empty_profile(cik: str, error: str) -> FilerProfile:
    return FilerProfile(
        cik=cik,
        name="",
        tickers=(),
        exchanges=(),
        sic="",
        sic_description="",
        state_or_country="",
        state_or_country_description="",
        latest_10k_date=None,
        latest_10k_accession=None,
        tenk_count=0,
        eightk_count=0,
        latest_8k_date=None,
        error=error,
    )
