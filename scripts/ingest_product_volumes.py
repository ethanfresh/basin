"""Ingest oil, gas and NGL volumes from the stored filings.

    python scripts/ingest_product_volumes.py

The ``companyfacts`` API drops every dimension, so the product split of
reserves and production is simply absent from it — a sweep of all 94 cached
payloads found no reserve volume tag that names a product. The filings
themselves are inline XBRL and keep the dimension, and they are already on
disk, so this reads the corpus and fetches nothing.

Only *dimensioned* facts are written. The undimensioned roll-up is already in
the store from companyfacts, and re-inserting it here would put two rows in
one cell.
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.documents import corpus
from basin.facts.concepts import BY_KEY
from basin.facts.instance import inline_facts
from basin.facts.xbrl import FactRow
from basin.store import DEFAULT_DB_PATH, connect, insert_facts

# Concept per XBRL tag. Reserve and production tags only: these are the
# disclosures filers dimension by product.
TAG_CONCEPT = {
    "srt:ProvedDevelopedAndUndevelopedReservesNet": "proved_reserves_boe",
    "srt:ProvedDevelopedAndUndevelopedReserveNetEnergy": "proved_reserves_boe",
    "us-gaap:ProvedDevelopedAndUndevelopedReservesNet": "proved_reserves_boe",
    "srt:ProvedDevelopedReservesBOE1": "proved_developed_reserves_boe",
    "srt:ProvedDevelopedReservesVolume": "proved_developed_reserves_boe",
    "us-gaap:ProvedDevelopedReservesVolume": "proved_developed_reserves_boe",
    "srt:ProvedUndevelopedReserveBOE1": "proved_undeveloped_reserves_boe",
    "srt:ProvedUndevelopedReserveVolume": "proved_undeveloped_reserves_boe",
    "us-gaap:ProvedUndevelopedReserveBOE1": "proved_undeveloped_reserves_boe",
    "srt:ProvedDevelopedAndUndevelopedReserveProductionEnergy": "production_volume",
    "srt:ProvedDevelopedAndUndevelopedReservesProduction": "production_volume",
    "us-gaap:ProvedDevelopedAndUndevelopedReserveProductionEnergy": "production_volume",
}


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    parser.add_argument("--corpus", type=Path, default=corpus.CORPUS)
    return parser.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    conn = connect(args.store)

    # Driven off the corpus, not off filing.primary_doc: the XBRL ingest
    # registers filings without a document name, so keying on primary_doc
    # silently reduced 1,200 stored filings to 123.
    filings = {
        r["accession"]: dict(r)
        for r in conn.execute(
            "SELECT accession, cik, form, filed_date, primary_doc FROM filing "
            "WHERE form LIKE '10-K%'"
        )
    }

    counts: collections.Counter = collections.Counter()
    written_total = 0

    for n, (accession, meta) in enumerate(sorted(filings.items()), 1):
        stored = [
            d for d in corpus.stored_documents(accession, root=args.corpus)
            if d.name.lower().endswith((".htm", ".html"))
        ]
        if not stored:
            counts["not in corpus"] += 1
            continue
        # The primary document is the largest one that is not an exhibit.
        named = meta.get("primary_doc")
        chosen = next((d for d in stored if d.name == named), None) or max(
            (d for d in stored if "ex" not in d.name.lower()),
            key=lambda d: d.size,
            default=None,
        )
        if chosen is None:
            counts["no primary document"] += 1
            continue
        raw = chosen.path.read_text(errors="replace")
        if "nonFraction" not in raw:
            counts["not inline XBRL"] += 1
            continue

        facts = inline_facts(raw, tuple(TAG_CONCEPT))
        rows = []
        for fact in facts:
            # Undimensioned totals already arrive via companyfacts.
            if fact.product is None:
                continue
            concept_key = TAG_CONCEPT.get(f"{fact.taxonomy}:{fact.tag}")
            if concept_key is None:
                continue
            spec = BY_KEY.get(concept_key)
            rows.append(
                FactRow(
                    cik=meta["cik"],
                    concept_key=concept_key,
                    taxonomy=fact.taxonomy,
                    tag=fact.tag,
                    value=fact.value,
                    unit=fact.unit,
                    product=fact.product,
                    unit_rank=spec.unit_rank(fact.unit) if spec else 0,
                    period_start=fact.period_start,
                    period_end=fact.period_end,
                    fiscal_year=int(fact.period_end[:4]) if fact.period_end else None,
                    fiscal_period="FY",
                    accession=accession,
                    form=meta["form"],
                    filed=meta["filed_date"],
                    # Distinguished from 'xbrl' so the panel can say where a
                    # product-split figure came from: the filing's own inline
                    # markup, not the flattened API.
                    extracted_by="xbrl:inline",
                )
            )
        if rows:
            written_total += insert_facts(conn, rows)
            counts["filings with product data"] += 1
            for row in rows:
                counts[row.product] += 1
        else:
            counts["no dimensioned volumes"] += 1

        if n % 100 == 0:
            conn.commit()
            print(f"  {n}/{len(filings)} filings, {written_total:,} rows", flush=True)

    conn.commit()
    print()
    for key, value in counts.most_common():
        print(f"  {key:<28}{value:>6}")
    print(f"  {'rows written':<28}{written_total:>6}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
