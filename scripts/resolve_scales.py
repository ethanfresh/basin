"""Resolve the true magnitude of reserve facts, in BOE.

    python scripts/resolve_scales.py

Verification records the scale a filing *prints* a figure at, which leaves two
candidate readings. This picks between them by testing the implied value per
barrel against the same filer's standardized measure, then writes the
canonical value and the evidence into ``fact_scale``.

The reserve concepts share the reserve table, so one decision per company and
period governs all three. Where the test cannot decide, nothing is written and
the cell stays uncomparable.
"""

from __future__ import annotations

import argparse
import collections
from pathlib import Path

from basin.facts.scale import STATUS_RESOLVED, resolve
from basin.facts.units import conversion_for
from basin.store import DEFAULT_DB_PATH, connect, record_scale

RESERVE_CONCEPTS = (
    "proved_reserves_boe",
    "proved_developed_reserves_boe",
    "proved_undeveloped_reserves_boe",
)


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--store", type=Path, default=DEFAULT_DB_PATH)
    args = parser.parse_args(argv)

    conn = connect(args.store)
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT f.id, f.cik, f.concept_key, f.value, f.unit, f.period_end,
                   v.scale_found, v.status AS verify_status
            FROM fact_current f
            LEFT JOIN fact_verification v ON v.fact_id = f.id
            WHERE f.concept_key IN (?, ?, ?, 'standardized_measure')
            """,
            RESERVE_CONCEPTS,
        )
    ]

    by_period: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in rows:
        by_period[(row["cik"], row["period_end"])].append(row)

    counts: collections.Counter = collections.Counter()
    for (cik, period), group in by_period.items():
        measure = next(
            (r for r in group if r["concept_key"] == "standardized_measure"), None
        )
        total = next((r for r in group if r["concept_key"] == "proved_reserves_boe"), None)
        anchor = total or next(
            (r for r in group if r["concept_key"] in RESERVE_CONCEPTS), None
        )
        if anchor is None:
            continue

        resolution = resolve(
            anchor["value"],
            anchor["unit"],
            anchor["scale_found"],
            measure["value"] if measure else None,
            measure["scale_found"] if measure else None,
        )
        counts[resolution.status] += 1
        if resolution.status != STATUS_RESOLVED:
            continue

        # One decision governs the whole reserve table for this period.
        for row in group:
            conversion = conversion_for(row["unit"])
            if conversion is None:
                continue
            is_measure = row["concept_key"] == "standardized_measure"
            divisor = resolution.measure_divisor if is_measure else resolution.reserve_divisor
            canonical = (row["value"] / divisor) * conversion.factor
            record_scale(
                conn,
                row["id"],
                divisor,
                canonical,
                conversion.canonical,
                basis=(
                    "monetary facts are tagged in dollars"
                    if is_measure
                    else f"implied ${resolution.usd_per_boe:,.2f}/BOE against the "
                    "standardized measure"
                ),
                conversion_note=conversion.note or None,
                usd_per_boe=resolution.usd_per_boe,
                rejected=resolution.rejected or None,
                note=resolution.note or None,
            )
    conn.commit()

    total_facts = conn.execute("SELECT COUNT(*) FROM fact_scale").fetchone()[0]
    print(f"company-periods: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    print(f"facts with a canonical value: {total_facts}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
