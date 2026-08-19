"""Measure how each cohort filer reports, on both axes that matter.

Two questions, two answers, neither of them inferable from domicile:

  * **reporting_taxonomy** -- which XBRL namespace the financials use, and so
    whether the Facts layer can read this filer at all. From ``companyfacts``.
  * **disclosure_regime** -- which reserve definitions apply, and so whether two
    extracted numbers can share a column. From the annual form the filer
    submits: 10-K and 20-F are SEC Subpart 1200, 40-F is Canadian NI 51-101.

Recorded per company so that an empty reserve cell can say *why* it is empty.
An IFRS filer's blank is the taxonomy having no such concept; a us-gaap filer's
blank is that filer not tagging it. They look identical in a coverage grid.

    python scripts/sync_taxonomy.py
    python scripts/sync_taxonomy.py --apply
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.edgar import EdgarClient, NotFound
from basin.edgar.discovery import submissions_url
from basin.facts import fetch_companyfacts
from basin.facts.taxonomy import detect_disclosure_regime, detect_reporting_taxonomy
from basin.store import DEFAULT_DB_PATH, connect
from basin.store.db import upsert_company


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.db)
    companies = conn.execute(
        "SELECT cik, ticker, name, country FROM company "
        "WHERE cohort IS NOT NULL ORDER BY ticker IS NULL, ticker"
    ).fetchall()
    print(f"measuring {len(companies)} cohort members\n")

    measured: list[tuple[str, str, str, str | None, str | None, str | None]] = []
    with EdgarClient() as client:
        for row in companies:
            payload = _safe_facts(client, row["cik"])
            taxonomy, tax_note = detect_reporting_taxonomy(payload)
            forms = _forms(client, row["cik"])
            regime, regime_note = detect_disclosure_regime(forms)
            measured.append(
                (row["cik"], row["ticker"] or "-", row["country"] or "?",
                 taxonomy, tax_note, regime)
            )
            conn_note = (regime_note or "")
            if args.apply:
                with conn:
                    upsert_company(
                        conn, row["cik"], row["name"],
                        reporting_taxonomy=taxonomy,
                        taxonomy_note=tax_note,
                        disclosure_regime=regime,
                        regime_note=conn_note or None,
                    )

    by_tax = collections.Counter(m[3] for m in measured)
    by_regime = collections.Counter(m[5] for m in measured)
    print("reporting taxonomy:", dict(by_tax))
    print("disclosure regime: ", dict(by_regime))

    # The interesting rows are the ones where the two axes disagree with what
    # domicile would have predicted.
    print("\n-- IFRS filers (Facts layer reaches revenue and capex only) --")
    for cik, ticker, country, tax, note, regime in measured:
        if tax == "ifrs-full":
            flag = "" if regime == "subpart-1200" else "   <- NI 51-101, not SEC-comparable"
            print(f"  {ticker:<6} {country:<16} {note}{flag}")

    print("\n-- foreign-domiciled but us-gaap (reachable like a domestic filer) --")
    for cik, ticker, country, tax, note, regime in measured:
        if tax == "us-gaap" and country not in ("USA", "?"):
            print(f"  {ticker:<6} {country:<16} {note}")

    if not args.apply:
        print("\nreport only; pass --apply to write")
    else:
        print(f"\napplied: {len(measured)} companies measured and recorded")
    return 0


def _safe_facts(client: EdgarClient, cik: str) -> dict | None:
    try:
        return fetch_companyfacts(client, cik)
    except (NotFound, Exception):
        return None


def _forms(client: EdgarClient, cik: str) -> list[str]:
    try:
        payload = client.get_json(submissions_url(cik))
    except Exception:
        return []
    return payload.get("filings", {}).get("recent", {}).get("form", [])


if __name__ == "__main__":
    raise SystemExit(main())
