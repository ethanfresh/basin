"""Finviz Elite screener export — the source of Basin's cohort assignments.

Basin groups companies into cohorts and forbids comparison across them, because
the metrics that make a peer table meaningful are not shared between, say, an
E&P operator and a midstream partnership: reserves and lifting cost do not apply
to a pipeline, and throughput and distribution coverage do not apply to a
driller. A cohort is therefore a KPI schema and a golden set, not a label.

Which cohort a filer belongs to was previously guessed by matching substrings
against its name ("royalt", "midstream", "pipeline"). Finviz maintains the
classification properly, so it replaces the guess.

The export endpoint returns CSV and is authenticated with an Elite token:

    https://elite.finviz.com/export/screener?v=111&f=<filters>&auth=<token>

Legacy ``.ashx`` URLs 301-redirect, so redirects are followed. The token is read
from the environment and never stored in the repository.
"""

from __future__ import annotations

import csv
import io
import os
import time
from dataclasses import dataclass

import httpx

EXPORT_URL = "https://elite.finviz.com/export/screener"

DEFAULT_TIMEOUT = 60.0

# Finviz issues no published rate limit for the export API. One request per
# industry is eight requests total, and they are spaced anyway.
REQUEST_INTERVAL = 0.4

# The energy cohorts, keyed by Finviz's screener filter slug. Each slug returns
# exactly one industry, which is asserted at parse time -- a slug that silently
# widened would contaminate a cohort with companies that do not belong in it.
#
# All eight are listed because the classification is what excludes a company as
# much as what includes one: knowing that a filer is Midstream is what keeps it
# out of a reserves table, and that is a decision worth being able to point at.
ENERGY_COHORTS: dict[str, str] = {
    "ind_oilgasdrilling": "Oil & Gas Drilling",
    "ind_oilgasep": "Oil & Gas E&P",
    "ind_oilgasequipmentservices": "Oil & Gas Equipment & Services",
    "ind_oilgasintegrated": "Oil & Gas Integrated",
    "ind_oilgasmidstream": "Oil & Gas Midstream",
    "ind_oilgasrefiningmarketing": "Oil & Gas Refining & Marketing",
    "ind_thermalcoal": "Thermal Coal",
    "ind_uranium": "Uranium",
}


class FinvizError(RuntimeError):
    """A request to the Finviz export API failed, or returned the wrong shape."""


@dataclass(frozen=True)
class ScreenerRow:
    """One security as Finviz classifies it."""

    ticker: str
    company: str
    sector: str
    industry: str
    country: str
    market_cap: float | None
    """In millions of USD, as Finviz exports it. None when Finviz reports none."""

    @property
    def is_usa(self) -> bool:
        return self.country.upper() == "USA"


def _auth_token() -> str:
    """The Finviz Elite API token.

    Kept in the environment rather than the source tree: it authenticates a paid
    account, and ``data/`` is gitignored but ``src/`` is not.
    """
    token = os.environ.get("FINVIZ_AUTH_TOKEN", "").strip()
    if not token:
        raise FinvizError(
            "FINVIZ_AUTH_TOKEN is not set. It is the Elite export API token "
            "from finviz.com/api. Set it in your environment or .env."
        )
    return token


def fetch_cohort(client: httpx.Client, slug: str) -> list[ScreenerRow]:
    """Every security Finviz places in the industry identified by *slug*."""
    expected = ENERGY_COHORTS.get(slug)
    params = {"v": "111", "f": slug, "auth": _auth_token()}
    response = client.get(EXPORT_URL, params=params, follow_redirects=True)
    if response.status_code != 200:
        raise FinvizError(f"{response.status_code} from Finviz for {slug}")
    return parse_export(response.text, expected_industry=expected)


def parse_export(body: str, *, expected_industry: str | None = None) -> list[ScreenerRow]:
    """Parse an export CSV. Pure -- no network, so it is testable."""
    reader = csv.DictReader(io.StringIO(body))
    rows: list[ScreenerRow] = []
    for raw in reader:
        industry = (raw.get("Industry") or "").strip()
        if expected_industry and industry != expected_industry:
            raise FinvizError(
                f"expected industry {expected_industry!r}, got {industry!r} "
                f"for {raw.get('Ticker')!r} -- the filter slug has changed meaning"
            )
        rows.append(
            ScreenerRow(
                ticker=(raw.get("Ticker") or "").strip().upper(),
                company=(raw.get("Company") or "").strip(),
                sector=(raw.get("Sector") or "").strip(),
                industry=industry,
                country=(raw.get("Country") or "").strip(),
                market_cap=_float(raw.get("Market Cap")),
            )
        )
    return rows


def fetch_energy_cohorts(
    client: httpx.Client | None = None, *, slugs: dict[str, str] | None = None
) -> dict[str, list[ScreenerRow]]:
    """Fetch every energy cohort, keyed by industry name."""
    slugs = slugs or ENERGY_COHORTS
    owned = client is None
    client = client or httpx.Client(timeout=DEFAULT_TIMEOUT)
    try:
        out: dict[str, list[ScreenerRow]] = {}
        for i, (slug, industry) in enumerate(slugs.items()):
            if i:
                time.sleep(REQUEST_INTERVAL)
            out[industry] = fetch_cohort(client, slug)
        return out
    finally:
        if owned:
            client.close()


def _float(value: str | None) -> float | None:
    try:
        return float(str(value).replace(",", ""))
    except (TypeError, ValueError):
        return None


# The cohorts Basin actually ingests: the ones whose companies produce oil, gas
# or NGL and therefore own reserves.
#
# The distinction is what the company owns, not what it handles. A driller and
# an equipment company sell services to producers. Midstream gathers, processes
# and fractionates -- it does extract NGLs, but from third-party gas under fee
# contracts, so it books throughput rather than reserves. A refiner buys crude
# and sells products. None of them have a reserve base, a lifting cost or a
# production volume, which is to say none of them have the metrics Basin's
# schema is built out of.
#
# Integrated is included because it produces, but it stays its own cohort rather
# than joining E&P: an integrated filer's production is one segment of a larger
# business, so its consolidated figures are not comparable to a pure-play E&P's
# without segment-level extraction that does not exist yet.
PRODUCING_COHORTS: dict[str, str] = {
    "ind_oilgasep": "Oil & Gas E&P",
    "ind_oilgasintegrated": "Oil & Gas Integrated",
}

# Securities Finviz places in an energy industry that are not energy companies.
# Finviz's classification is far better than SIC -- 252 of 253 tickers resolved
# to a CIK on the first pull -- but it is not clean either, and a structured
# note filed under an operating industry would otherwise be ingested as one.
# Securities Finviz places in a producing industry that hold no reserves.
#
# Each of these failed the producer check in basin.facts.producer -- no reserve
# or production concepts tagged, and no reserve language in the annual report
# that was read. They are excluded here rather than filtered downstream so that
# the reason travels with the decision.
EXCLUDED_TICKERS: dict[str, str] = {
    "MHM": "Bank of America structured note, not an energy operator",
    "TGS": "Transportadora de Gas del Sur -- Argentine gas pipeline; EDGAR "
           "registers it as GAS TRANSPORTER OF THE SOUTH INC. 0 reserve-language "
           "hits in 1.27M characters of its 20-F; tags only revenue and capex",
    "SLNG": "Stabilis Solutions -- small-scale LNG production and distribution, "
            "not upstream. 0 reserve-language hits in its 10-K",
    "VIVK": "Vivakor -- oilfield waste remediation and crude transport. "
            "0 reserve-language hits in its 10-K",
    "CKX": "CKX Lands -- Louisiana land company that leases acreage rather "
           "than operating it. Its 10-K states the position outright: reserve "
           "information \"is not available. A schedule indicating such reserve "
           "quantities is, therefore, not presented.\" Tags only revenue and capex",
    "NRT": "North European Oil Royalty Trust -- passive royalty on German "
           "concessions. Its 4 reserve-language hits are all risk-factor prose "
           "about the underlying assets depleting; it discloses no reserve "
           "quantities of its own, unlike the US royalty trusts in the cohort, "
           "which publish full reserve tables",
}
