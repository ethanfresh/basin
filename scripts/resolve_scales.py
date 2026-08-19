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

from basin.facts.scale import MAX_USD_PER_BOE, MIN_USD_PER_BOE, STATUS_RESOLVED, resolve
from basin.facts.units import conversion_for
from basin.store import DEFAULT_DB_PATH, connect, record_scale
from basin.store.db import clear_scale

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
                   f.product,
                   -- A figure read off the printed table is printed at the
                   -- scale it is stored at: the row IS the page. There is no
                   -- divisor to infer, so it is declared rather than searched
                   -- for, and the value-per-barrel test is left to do the one
                   -- job it is good at -- choosing the unit.
                   CASE WHEN f.extracted_by = 'table:reserves' THEN 1.0
                        ELSE v.scale_found END AS scale_found,
                   v.status AS verify_status, v.units_nearby,
                   --
                   -- Only the table rows get the zero. A `scale_declared` of
                   -- 0 already sits on 2,333 XBRL rows, where it was inferred
                   -- from markup this resolver was tuned against; honouring
                   -- those too costs 80 currently-resolved cells across 13
                   -- company-periods, and whether that trade is right is a
                   -- separate question from this one. Left alone deliberately.
                   CASE WHEN f.extracted_by = 'table:reserves' THEN 0
                        ELSE NULLIF(v.scale_declared, 0) END AS scale_declared
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
            (anchor["units_nearby"] or "").split("|") if anchor["units_nearby"] else (),
            declared_scale=anchor["scale_declared"],
        )
        counts[resolution.status] += 1
        if resolution.status != STATUS_RESOLVED:
            # Clear anything an earlier run stored for this period. A period
            # that no longer resolves must not keep showing a magnitude that a
            # previous, worse resolver produced -- the panel would rank a value
            # the current code refuses to stand behind.
            for row in group:
                clear_scale(conn, row["id"])
            continue

        # The decision governs the rows it was actually made about.
        #
        # It used to govern every reserve row in the period, on the reasoning
        # that the reserve lines share a table and therefore share its unit and
        # scale. That holds for a filer reporting one reserve table. It fails
        # for one reporting by product: W&T's 2025 rows arrive as 6.7 MMBoe,
        # 11.7 and 38.7 MMBbls, and 423,300,000,000 ft3 -- 423.3 Bcf of gas,
        # correctly tagged. Forcing the anchor's MMBoe onto the ft3 row read
        # 423.3 billion cubic feet as 423.3 billion million BOE: 4.2e17, more
        # than world reserves.
        #
        # So the resolved unit is applied only where the row declared the same
        # unit as the anchor -- where they demonstrably are the same table. A
        # row that declared something else keeps its own unit and is read as
        # tagged, which is the reading its own declaration supports.
        # Scale and unit are shared to different extents, and conflating them
        # is what broke this.
        #
        # The divisor is a property of the printed table -- "in thousands" at
        # the top of a page applies to every line under it -- so it carries to
        # every reserve row in the period. The unit does not: a filer reporting
        # by product prints oil in MBbls, gas in MMcf and the total in MBoe in
        # the same table. Viper's 2024 oil line is 93,563,000 MBbls, and reading
        # it in the total's MBoe made 93.5 billion BOE out of 93.5 million.
        #
        # So the resolved unit applies only where the row declared the same unit
        # as the anchor, and every reserve row takes the resolved divisor.
        anchor_unit = anchor["unit"]

        # The period's total proved reserves, in canonical units, if it has one.
        # Taken from the row with no product dimension -- the whole reserve base
        # rather than one of its parts.
        period_total = None
        total_row = next(
            (r for r in group
             if r["concept_key"] == "proved_reserves_boe" and not r["product"]),
            None,
        )
        if total_row is not None:
            total_conv = conversion_for(
                resolution.reserve_unit if total_row["unit"] == anchor_unit
                else total_row["unit"]
            )
            if total_conv is not None and total_conv.canonical != "USD":
                period_total = (
                    total_row["value"] / resolution.reserve_divisor
                ) * total_conv.factor

        # A declared unit that is wrong for one product line is wrong for the
        # whole line item, so the rows are judged together rather than one at a
        # time.
        #
        # Range's 2021 reserve table prints gas in MMcf and both oil and NGL in
        # MMBbls. Read as tagged the NGL line is 5.8e11 BOE and rejected, while
        # the oil line is 2.38e10 -- under the ceiling, and $0.52/BOE against
        # the standardized measure, so it survived every test applied to it
        # alone and ranked first in the panel. They are the same label in the
        # same table: the one that fails convicts the other.
        #
        # Nothing is corrected. Basin does not rewrite a filer's unit, so the
        # outcome is an unresolved cell, which is what the tagging supports.
        discredited: set[str] = set()
        for row in group:
            if row["concept_key"] == "standardized_measure":
                continue
            unit = row["unit"]
            conversion = conversion_for(unit)
            if conversion is None or conversion.canonical == "USD":
                continue
            reading = (row["value"] / resolution.reserve_divisor) * conversion.factor
            if not reading:
                continue
            # Either test convicts the label. Talos prints oil and NGL both in
            # MMBbls: the oil line implies $0.05/BOE and is rejected, while the
            # NGL line implies $0.34 and clears the floor by four cents. One
            # label, one table, one verdict.
            if abs(reading) > 1e11:
                discredited.add(unit)
            elif measure is not None and measure["value"] / reading < MIN_USD_PER_BOE:
                # Only the low side indicts the label, and the asymmetry is the
                # point. A reading that implies too few dollars per barrel says
                # the barrels are too many, which is what a unit inflated by a
                # thousand looks like. A reading that implies a lot of dollars
                # per barrel says the barrels are few -- which is simply what a
                # minor product line is, and testing it against the measure for
                # the whole reserve base would convict every small component.
                discredited.add(unit)

        for row in group:
            is_measure = row["concept_key"] == "standardized_measure"
            if not is_measure and row["unit"] in discredited:
                counts["rejected: unit implausible for a sibling line"] += 1
                clear_scale(conn, row["id"])
                continue
            if is_measure:
                unit, divisor = row["unit"], resolution.measure_divisor
            else:
                same_unit = row["unit"] == anchor_unit
                unit = (resolution.reserve_unit or row["unit"]) if same_unit else row["unit"]
                divisor = resolution.reserve_divisor
            conversion = conversion_for(unit)
            if conversion is None:
                continue
            canonical = (row["value"] / divisor) * conversion.factor
            # Nothing in this cohort holds 1e11 BOE; past that the unit is
            # wrong, not the company large.
            #
            # Clear any magnitude already stored rather than only declining to
            # write one. Skipping leaves a value from an earlier, worse run in
            # place, which is how W&T's 4.2e17 survived the guard that was
            # added to catch exactly it.
            if conversion.canonical != "USD" and abs(canonical) > 1e11:
                counts["rejected as implausible"] += 1
                clear_scale(conn, row["id"])
                continue

            # Every reserve row gets the value-per-barrel test, not only the
            # anchor the period was resolved from.
            #
            # A filer can label its product lines wrongly while the line the
            # anchor came from is right. Range's 2019 gas is 12,114,977 MMcf,
            # correct, and its oil is 74,532 tagged MMBbls when the table is
            # printed in MBbls -- 74.5 billion barrels instead of 74.5 million.
            # Sharing the anchor's scale cannot catch that, because the fault is
            # in the row's own unit.
            #
            # The band is the wide one. A single product is not expected to
            # imply the same value per barrel as the whole reserve base, so this
            # is a check for order-of-magnitude wrongness, not for agreement.
            if conversion.canonical != "USD" and measure is not None and canonical:
                implied = measure["value"] / canonical
                if not (MIN_USD_PER_BOE <= implied <= MAX_USD_PER_BOE):
                    counts["rejected on value per barrel"] += 1
                    clear_scale(conn, row["id"])
                    continue
            # A product line cannot exceed the reserve base it is part of.
            #
            # The value-per-barrel band catches an order of magnitude; it does
            # not catch a component that is merely far too large relative to its
            # own filer. Range's 2019 oil line resolves to 21.3 billion BOE
            # against a total reserve base near 2 billion -- inside the band,
            # because a wrongly-scaled component and a correctly-scaled measure
            # can still produce a plausible-looking ratio.
            #
            # This is the identity the reserve_consistency view already tests,
            # applied at resolution time: developed <= total, and a product is a
            # subset of the whole. Only checked where the period actually has a
            # total to check against.
            if (not is_measure) and row["product"] and period_total is not None:
                if canonical > period_total * 1.05:
                    counts["rejected: component exceeds total"] += 1
                    clear_scale(conn, row["id"])
                    continue

            relabelled = (not is_measure) and unit != row["unit"]
            record_scale(
                conn,
                row["id"],
                divisor,
                canonical,
                conversion.canonical,
                basis=(
                    "monetary facts are tagged in dollars"
                    if is_measure
                    else (
                        f"implied ${resolution.usd_per_boe:,.2f}/BOE against the "
                        "standardized measure"
                        + (
                            f"; unit read as {unit} from the filing's table header, "
                            f"not the tagged {row['unit']}"
                            if relabelled
                            else ""
                        )
                    )
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
