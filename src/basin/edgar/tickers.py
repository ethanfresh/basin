"""The SEC's canonical ticker map, and the choice of one ticker per filer.

Basin presents companies by ticker and keys them by CIK. That split exists
because the two identifiers fail in opposite directions: a CIK is assigned once
and never reused, while a ticker is released when a company delists and can be
reassigned to an unrelated filer later. Keying facts on a ticker would let two
companies' histories merge without erroring, which is precisely the failure the
append-only fact store is built to prevent.

The submissions API carries a ``tickers`` field, but it is empty for filers that
are no longer listed and, in practice, for some that are. The authoritative map
is a separate file:

    https://www.sec.gov/files/company_tickers.json

which lists only *currently listed* securities. Absence from it is therefore
information — it means the filer has no live listing — and not an error.
"""

from __future__ import annotations

from dataclasses import dataclass

from basin.edgar.client import SEC_WWW_HOST, EdgarClient, cik_padded

COMPANY_TICKERS_URL = f"{SEC_WWW_HOST}/files/company_tickers.json"

# Suffixes the SEC appends to a warrant, unit or right sharing a filer's CIK.
# Occidental's CIK carries both OXY and OXY-WT; AleAnna's carries ANNA and
# ANNAW. The common stock is the one Basin means by "the ticker".
_DERIVATIVE_SUFFIXES = ("-WT", "-WS", "-RT", "-U", "-UN")
_DERIVATIVE_TRAILING = ("W", "R", "U")


@dataclass(frozen=True)
class TickerMap:
    """CIK -> every currently listed ticker on that filer, in SEC order."""

    by_cik: dict[str, tuple[str, ...]]

    def primary(self, cik: int | str) -> str | None:
        """The common-stock ticker for *cik*, or None if it is not listed."""
        listed = self.by_cik.get(cik_padded(cik))
        return primary_ticker(listed) if listed else None

    def __len__(self) -> int:
        return len(self.by_cik)


def primary_ticker(tickers: tuple[str, ...] | list[str]) -> str | None:
    """Pick the common-stock ticker out of the securities sharing one CIK.

    Warrants and units are filtered first; if that leaves nothing (a filer whose
    only listed security is a warrant) the filter is abandoned rather than
    returning None, because reporting *a* ticker beats reporting none. Ties
    break on length, then alphabetically, so the choice is deterministic.
    """
    if not tickers:
        return None
    common = [t for t in tickers if not _is_derivative(t)] or list(tickers)
    return min(common, key=lambda t: (len(t), t))


def _is_derivative(ticker: str) -> bool:
    upper = ticker.upper()
    if upper.endswith(_DERIVATIVE_SUFFIXES):
        return True
    # ANNA/ANNAW, NUAI/NUAIW -- a warrant is the common ticker plus a letter.
    return len(upper) == 5 and upper.endswith(_DERIVATIVE_TRAILING)


def fetch_ticker_map(client: EdgarClient) -> TickerMap:
    """Fetch and index the SEC's company_tickers.json.

    The file is a JSON object keyed by row number, not a list, so it is the
    values that matter. One CIK can appear on several rows.
    """
    payload = client.get_json(COMPANY_TICKERS_URL)
    return ticker_map_from_payload(payload)


def ticker_map_from_payload(payload: dict) -> TickerMap:
    """Index a company_tickers.json payload. Pure -- no network, so testable."""
    by_cik: dict[str, list[str]] = {}
    for row in payload.values():
        cik = cik_padded(row["cik_str"])
        ticker = str(row["ticker"]).strip().upper()
        if ticker and ticker not in by_cik.setdefault(cik, []):
            by_cik[cik].append(ticker)
    return TickerMap({cik: tuple(ts) for cik, ts in by_cik.items()})
