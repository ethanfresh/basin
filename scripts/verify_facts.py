"""Check stored facts against the filings they cite.

    export BASIN_SEC_USER_AGENT="Basin research (you@example.com)"
    python scripts/verify_facts.py --limit 200

Fetches each cited filing's primary document once, caches it, and searches for
every value that filing is supposed to support. Records whether the figure was
found and at what scale, because the document is the only place a filing's
presentation scale is actually stated.

Documents are large — a 10-K runs to several megabytes — so work is grouped by
accession and the cache is worth keeping.
"""

from __future__ import annotations

import argparse
import collections
import sys
from pathlib import Path

from basin.documents import corpus as corpus_store
from basin.documents import find_value, primary_document
from basin.documents.locate import is_wrapper_form, substantive_exhibits
from basin.documents.inline import match_fact, tagged_figures
from basin.documents.tables import header_for_value, parse_tables
from basin.documents.headers import unit_hints
from basin.documents.verify import searchable
from basin.documents.corpus import fetch as fetch_document
from basin.documents.text import parse, section_of, snippet
from basin.edgar import EdgarClient, NotFound, SECError
from basin.store import DEFAULT_DB_PATH, connect, record_verification




def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--limit", type=int, default=200, help="facts to check")
    parser.add_argument("--concept", action="append", default=[])
    parser.add_argument("--since", default="2023-01-01", help="minimum period_end")
    parser.add_argument("--recheck", action="store_true")
    parser.add_argument("--retry-failed", action="store_true",
                        help="re-check only facts previously recorded not_found")
    return parser.parse_args(argv)


def load_documents(client: EdgarClient, cik: str, accession: str,
                   form: str | None = None):
    """Every stored document for a filing, primary first.

    D4. Verification used to read only the primary document, leaving 609
    stored exhibits unsearched -- and the EX-99.1 earnings release is exactly
    where guidance and per-unit costs are announced. Exhibits are searched
    after the primary, so a figure in both still cites the filing proper.

    Stored exhibits are not enough on their own. What sits in the corpus is
    whatever a previous fetch pass happened to collect, so verification was
    silently bounded by another script's scope: a 40-F or 6-K whose exhibits
    were never fetched had only its cover sheet to search, and every figure in
    it recorded as not found. 186 revenue and capex facts failed this way, 125
    of them on those two forms. For a wrapper form the exhibits are now read
    from the filing index and fetched on demand, so verification depends on the
    filing rather than on what was downloaded earlier.
    """
    primary = primary_document(cik, accession, client=client)
    stored = [
        d.name
        for d in corpus_store.stored_documents(accession)
        if d.name.lower().endswith((".htm", ".html"))
    ]
    if is_wrapper_form(form) and len(stored) <= 1:
        try:
            for name in substantive_exhibits(client, cik, accession):
                if name not in stored:
                    stored.append(name)
        except (NotFound, SECError):
            pass
    if primary and primary not in stored:
        stored.insert(0, primary)
    ordered = ([primary] if primary in stored else []) + [
        n for n in stored if n != primary
    ]
    if not ordered:
        return []

    out = []
    for name in ordered:
        try:
            raw = fetch_document(client, cik, accession, name)
        except (NotFound, SECError):
            continue
        out.append((name, raw, parse(raw)))
    return out


_TABLE_CACHE: dict[int, list] = {}


def _tables_for(raw: str) -> list:
    """Tables of a document, parsed once per document rather than per fact.

    A filing with 59 facts was re-parsing its tables 59 times, which turned a
    ten-minute pass into forty. Keyed by identity because the raw strings are
    already held for the duration of the run.
    """
    key = id(raw)
    if key not in _TABLE_CACHE:
        _TABLE_CACHE[key] = parse_tables(raw)
    return _TABLE_CACHE[key]


def locate_fact(fact: dict, loaded: list) -> dict:
    """Locate one fact, preferring the filing's markup over a string search.

    D1/D3. The markup identifies the fact -- concept, period, product, unit and
    declared scale -- so it resolves to the one occurrence that carries it.
    A string search only knows what the number looks like, and across the store
    two thirds of those matches were one of several identical candidates.
    """
    concept_tag = fact.get("tag")
    for name, raw, parsed in loaded:
        if "nonFraction" not in raw:
            continue
        figure = match_fact(
            tagged_figures(raw),
            concept_tag=concept_tag,
            period_end=fact["period_end"],
            value=fact["value"],
            product=fact.get("product"),
        )
        if figure is None:
            continue
        line = parsed.locate_raw(figure.start)
        # D2. The column header governing this figure, as a cross-check on the
        # unit it claims. Gulfport tags 3,612 Bcf of gas as "bbl"; the header
        # above it says Natural Gas (Bcf), and that is the honest answer.
        #
        # What gets stored is the unit tokens extracted from the header, not
        # the header prose: "Natural Gas Equivalent (Bcfe)" is a fine display
        # string and a useless unit candidate, and the resolver consumes this
        # column as candidates.
        header = header_for_value(_tables_for(raw), figure.shown, near=figure.start)
        header_units: list[str] = []
        if header and header[0]:
            probe = f"{header[0]} 0"
            header_units = [
                h.unit for h in unit_hints(probe, len(header[0]) + 1, 1)
            ]
        return {
            "status": "found",
            "method": "markup",
            "units_nearby": "|".join(header_units) or None,
            "note": (f"column: {header[0]}; row: {header[1]}" if header and header[0] else None),
            "document": name,
            "printed": figure.shown,
            "scale_found": 10**figure.scale if figure.scale else 1.0,
            "scale_label": f"declared 10^{figure.scale}" if figure.scale else "as tagged",
            "scale_declared": figure.scale,
            "hits": 1,
            "anchor": figure.anchor,
            "source_span": snippet(parsed.text, line.start, line.end) if line else None,
            "char_offset": line.start if line else None,
            "line_no": line.line if line else None,
            "page": line.page if line else None,
            "folio": parsed.folio(line.page) if line else None,
            "section": section_of(parsed.text, line.start) if line else None,
            "line_text": line.text[:400] if line else None,
        }

    # Fallback: the filer did not tag this figure, so match the printed string.
    for name, raw, parsed in loaded:
        match = find_value(parsed.text, fact["value"])
        if match is None:
            continue
        line = parsed.locate(match.offset)
        return {
            "status": "found",
            "method": "text",
            "document": name,
            "printed": match.printed,
            "scale_found": match.scale,
            "scale_label": match.scale_label,
            "hits": match.hits,
            "source_span": match.source_span,
            "char_offset": match.offset,
            "line_no": getattr(match, "line", None),
            "page": line.page if line else None,
            "folio": parsed.folio(line.page) if line else None,
            "section": getattr(match, "section", None),
            "line_text": line.text[:400] if line else None,
            "units_nearby": "|".join(getattr(match, "units_nearby", ()) or ()) or None,
        }

    # D7. "Cannot be searched for" is not the same claim as "absent from the
    # filing", and recording the second when the first is true reads as an
    # accusation against the filer.
    if not searchable(fact["value"]):
        return {
            "status": "unverifiable",
            "document": loaded[0][0] if loaded else None,
            "note": "value has too few significant digits to search for",
        }
    return {"status": "not_found", "document": loaded[0][0] if loaded else None}


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)

    sql = """
        SELECT f.id, f.cik, f.concept_key, f.value, f.unit, f.period_end,
               f.accession, f.form,
               -- locate_fact matches the filing's markup on these two. They
               -- were missing from this list while it read them, so every
               -- markup match ran on value and period alone -- and the guard
               -- in match_fact that refuses a figure carrying the wrong
               -- concept is written as `if concept_tag`, so withholding the
               -- tag turned it off.
               f.tag, f.product
        FROM fact_current f
        WHERE f.period_end >= ?
    """
    params: list = [args.since]
    if args.concept:
        sql += f" AND f.concept_key IN ({','.join('?' * len(args.concept))})"
        params += args.concept
    if args.retry_failed:
        sql += (" AND f.id IN (SELECT fact_id FROM fact_verification "
                "WHERE status = 'not_found')")
    elif not args.recheck:
        sql += " AND f.id NOT IN (SELECT fact_id FROM fact_verification)"
    # Grouped by accession so each document is fetched once.
    sql += " ORDER BY f.accession, f.concept_key LIMIT ?"
    params.append(args.limit)

    facts = [dict(r) for r in conn.execute(sql, params)]
    if not facts:
        print("nothing to verify")
        return 0

    by_accession: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for fact in facts:
        by_accession[(fact["cik"], fact["accession"])].append(fact)

    counts: collections.Counter = collections.Counter()
    scales: collections.Counter = collections.Counter()
    methods: collections.Counter = collections.Counter()

    try:
        with EdgarClient() as client:
            for n, ((cik, accession), group) in enumerate(sorted(by_accession.items()), 1):
                try:
                    loaded = load_documents(
                        client, cik, accession, form=group[0].get("form")
                    )
                except (NotFound, SECError) as exc:
                    for fact in group:
                        record_verification(conn, fact["id"], "unavailable", note=str(exc)[:200])
                        counts["unavailable"] += 1
                    continue

                if not loaded:
                    for fact in group:
                        record_verification(
                            conn, fact["id"], "unavailable", note="no primary document"
                        )
                        counts["unavailable"] += 1
                    continue

                for fact in group:
                    outcome = locate_fact(fact, loaded)
                    record_verification(conn, fact["id"], **outcome)
                    counts[outcome["status"]] += 1
                    if outcome["status"] == "found":
                        methods[outcome.get("method") or "text"] += 1
                        if outcome.get("scale_label"):
                            scales[outcome["scale_label"]] += 1
                conn.commit()
                print(f"  [{n}/{len(by_accession)}] {accession}  {len(group)} facts", flush=True)
    except SECError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    finally:
        conn.commit()
        conn.close()

    total = sum(counts.values())
    print(f"\n{'=' * 56}\nverified {total} facts across {len(by_accession)} filings")
    for status, n in counts.most_common():
        print(f"  {status:<14}{n:>5}  {n / total:>5.0%}")
    if methods:
        print("\nhow each figure was located:")
        for label, n in methods.most_common():
            print(f"  {label:<40}{n:>5}  {n / max(1, sum(methods.values())):>5.0%}")
    if scales:
        print("\nscale the document printed the figure at:")
        for label, n in scales.most_common():
            print(f"  {label:<40}{n:>5}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
