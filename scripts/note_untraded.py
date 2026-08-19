"""Record which filers are out of scope because they are not traded.

Basin's scope is traded US securities. Cohort membership comes from a screener,
and a screener returns listed securities, so a producer with no live listing is
excluded automatically -- silently, and without leaving any trace that it exists.

That is the wrong kind of boundary. Continental Resources went private in 2022
and still files 10-Ks against its public debt; Energy 11 and Energy Resources 12
are non-traded partnerships filing every year. They are producers, they are
current, and no screener will return them.

This marks every filer in the store with why it is or is not eligible, so the
gap is a measured number rather than something discovered later. Nothing is
deleted and no cohort changes -- the scope decision stands.

    python scripts/note_untraded.py
    python scripts/note_untraded.py --apply
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.edgar import EdgarClient, NotFound, SECError
from basin.edgar.discovery import submissions_url
from basin.edgar.tickers import fetch_ticker_map
from basin.facts.producer import RESERVE_CONCEPTS
from basin.store import DEFAULT_DB_PATH, connect

LISTED = "listed"
PRIVATE_FILER = "private-filer"
DEREGISTERED = "deregistered"
SUPERSEDED = "superseded"

# Periodic reports. Continuing to file one of these is what separates a company
# that went private from one that ceased to exist.
PERIODIC_FORMS = ("10-K", "10-Q", "20-F", "40-F")

# Form 15 certifies termination of registration -- the filer is telling the SEC
# it intends to stop reporting. It is the only unambiguous "this is over"
# marker EDGAR has; Form 25 is delisting from an exchange, which a company can
# survive. Continental filed both in 2022-23 and has filed a 10-K every year
# since, because its public debt keeps the obligation alive.
DEREGISTRATION_FORMS = ("15-12B", "15-12G", "15F-12B", "15F-12G")


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--db", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--apply", action="store_true")
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.db)

    superseded = {
        r["successor_cik"]
        for r in conn.execute(
            "SELECT successor_cik FROM registrant_succession WHERE status = 'resolved'"
        )
    }
    companies = conn.execute(
        "SELECT cik, ticker, name, cohort FROM company ORDER BY name"
    ).fetchall()

    with EdgarClient() as client:
        ticker_map = fetch_ticker_map(client)
        rows = []
        for company in companies:
            cik = company["cik"]
            # Cohort membership is itself proof of a listing -- it comes from a
            # screener of traded securities. It has to count, because the SEC's
            # ticker map points a superseded company's symbol at the successor:
            # XOM maps to CIK 2115436 while the ticker, the facts and the cohort
            # membership all sit on predecessor 34088, which would otherwise be
            # reported as an unreachable filer.
            listed = ticker_map.primary(cik) is not None or company["cohort"] is not None
            last_filed, last_periodic, deregistered_on = _filing_history(client, cik)
            concepts = [
                r[0] for r in conn.execute(
                    f"""SELECT DISTINCT concept_key FROM fact WHERE cik = ?
                        AND concept_key IN ({','.join('?' * len(RESERVE_CONCEPTS))})""",
                    (cik, *RESERVE_CONCEPTS),
                )
            ]
            rows.append((company, listed, last_filed, concepts,
                         last_periodic, deregistered_on))

    out = []
    for company, listed, last_filed, concepts, last_periodic, dereg_on in rows:
        cik = company["cik"]
        if cik in superseded:
            status, note = SUPERSEDED, "registrant replaced by a successor"
        elif listed:
            status, note = LISTED, None
        elif dereg_on and not (last_periodic and last_periodic > dereg_on):
            # Filed Form 15 and stopped reporting. The company was acquired or
            # wound up; it is not a gap in coverage, it is a company that no
            # longer exists to cover.
            status = DEREGISTERED
            note = (f"deregistered {dereg_on}; last periodic report "
                    f"{last_periodic or 'none'}")
        else:
            # No listing and still filing periodic reports. This is the real
            # gap: a producer reporting to the SEC that no screener returns.
            status = PRIVATE_FILER
            note = (f"no listing, still filing -- last periodic report "
                    f"{last_periodic or 'none'}")
            if dereg_on:
                note += f" (filed Form 15 in {dereg_on[:4]} and kept reporting)"
        out.append((cik, company["name"], status, last_filed, note,
                    bool(concepts), last_periodic, dereg_on))

    counts = collections.Counter(r[2] for r in out)
    print(f"listed {counts[LISTED]} | private filers {counts[PRIVATE_FILER]} "
          f"| deregistered {counts[DEREGISTERED]} | superseded {counts[SUPERSEDED]}\n")

    missed = [r for r in out if r[2] == PRIVATE_FILER and r[5]]
    print("=" * 78)
    print(f"PRODUCERS THIS SCOPE CANNOT REACH -- {len(missed)}")
    print("  no listing, still filing periodic reports. A real gap in coverage.")
    print("=" * 78)
    for cik, name, _s, _last, _n, _c, periodic, dereg in missed:
        mark = f"  (Form 15 in {dereg[:4]}, kept reporting)" if dereg else ""
        print(f"  {name[:44]:<44} last 10-K/Q {periodic}{mark}")

    gone = [r for r in out if r[2] == DEREGISTERED]
    print("\n" + "=" * 78)
    print(f"ACQUIRED OR WOUND UP -- {len(gone)}")
    print("  filed Form 15 and stopped reporting. Not a gap; the filer is gone.")
    print("=" * 78)
    for cik, name, _s, _last, _n, _c, periodic, dereg in gone:
        print(f"  {name[:44]:<44} deregistered {dereg}, last 10-K/Q {periodic or 'none'}")

    if not args.apply:
        print("\nreport only; pass --apply to write")
        return 0

    with conn:
        for cik, _name, status, last, note, _c, _p, _d in out:
            conn.execute(
                "UPDATE company SET listing_status = ?, last_filing_date = ?, "
                "listing_note = ? WHERE cik = ?",
                (status, last, note, cik),
            )
    print(f"\napplied: {len(out)} companies marked")
    return 0


def _filing_history(client: EdgarClient, cik: str) -> tuple[str | None, str | None, str | None]:
    """``(last filing, last periodic report, deregistration date)``.

    All three are needed together, because the useful question is not whether a
    filer went quiet but whether it kept *reporting* after telling the SEC it
    would stop. A company that files Form 15 and then files nothing was
    acquired. One that files Form 15 and keeps filing 10-Ks is private and
    still operating.
    """
    try:
        payload = client.get_json(submissions_url(cik))
    except (NotFound, SECError):
        return None, None, None

    recent = payload.get("filings", {}).get("recent", {})
    forms = recent.get("form", [])
    dates = recent.get("filingDate", [])

    last_filed = max(dates) if dates else None
    periodic = [d for d, f in zip(dates, forms) if f.split("/")[0] in PERIODIC_FORMS]
    dereg = [d for d, f in zip(dates, forms) if f.split("/")[0] in DEREGISTRATION_FORMS]
    return last_filed, (max(periodic) if periodic else None), (max(dereg) if dereg else None)


if __name__ == "__main__":
    raise SystemExit(main())
