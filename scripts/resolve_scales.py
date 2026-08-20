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

# Concepts whose canonical form follows from the declaration, with nothing to
# infer and so nothing to adjudicate.
#
# The value-per-barrel machinery below exists because a tagged reserve leaves
# two questions open: what divisor the page applied, and which unit the filer
# meant. Neither is open here.
#
# A currency needs no unit conversion -- USD is already canonical -- and the
# only remaining question, the divisor, is not open either: an XBRL value is
# absolute, and a figure read off a printed table is the figure as printed with
# its column header for a unit. A volume read off a table is the same case: the
# header states the unit, so there is no filer declaration to distrust.
#
# What is deliberately NOT here is a volume tagged in XBRL. That is exactly
# where a filer's declared unit is unreliable -- Gulfport tags 3,612 Bcf of gas
# as "bbl" -- and resolving it needs the corroboration the reserve path gets
# from the standardized measure. Left unresolved rather than guessed.
DIRECT_CONCEPTS = (
    "capex",
    "oil_and_gas_revenue",
    # The reserve resolver reads a standardized measure to test reserves
    # against, and clears the whole period when that test cannot be made. That
    # is right for the reserve volumes, whose magnitude depended on the test,
    # and wrong for the measure itself: it is a dollar figure printed on a
    # page, and whether the barrels beside it could be read says nothing about
    # it. Filled here only where the resolver left it blank.
    "standardized_measure",
)


# A year's production against the reserves it came out of. Reserve life over
# the 232 production facts that already have a magnitude and a same-product
# reserve to check against runs 1.1 years at the low end to 18 at the third
# quartile, with a tenth of a percent above 200 -- and everything past that is
# in the millions, which is a misread column rather than a long-lived asset.
# The band admits any real reserve life, including a very long-lived gas asset,
# and catches a reading wrong by three orders of magnitude or more.
MIN_RESERVE_LIFE_YEARS = 0.5
MAX_RESERVE_LIFE_YEARS = 200.0


def resolve_production(conn, counts) -> None:
    """Record production volumes, corroborated against the filer's own reserves.

    A production volume has the same problem a reserve volume has -- the filer
    declares the unit and the declaration cannot be taken at face value -- but
    none of the standardized measure's help in solving it: the measure values a
    reserve base, not a year's output.

    What production does have is the reserve base itself, already resolved. A
    year's production divided into proved reserves is reserve life, and that is
    a quantity with a known range. So a declared unit that implies a plausible
    reserve life is corroborated by the filer's own disclosure, and one that
    implies half a million years is not.

    The comparison is like for like -- the same product on both sides, or
    undimensioned against undimensioned. Measuring one product line against the
    whole reserve base is what makes an NGL line look like a 400-year asset, and
    it is the same asymmetry the value-per-barrel test is careful about.

    A figure read off a printed table keeps the treatment it has elsewhere: its
    unit is the column header standing over it, which is evidence, so it
    resolves even where no reserve is available to test it. A unit tagged in
    XBRL is a declaration and nothing more, so with no reserve to check it
    against it stays unresolved rather than trusted.
    """
    reserves = {
        (r["cik"], r["period_end"], r["product"] or ""): r["canonical_value"]
        for r in conn.execute(
            """
            SELECT f.cik, f.period_end, f.product, s.canonical_value
            FROM fact f JOIN fact_scale s ON s.fact_id = f.id
            WHERE f.concept_key = 'proved_reserves_boe' AND s.canonical_value > 0
            """
        )
    }
    rows = [
        dict(r)
        for r in conn.execute(
            """
            SELECT f.id, f.cik, f.period_end, f.product, f.value, f.unit,
                   f.extracted_by
            FROM fact_current f
            WHERE f.concept_key = 'production_volume'
            """
        )
    ]
    for row in rows:
        conversion = conversion_for(row["unit"])
        if conversion is None or conversion.canonical == "USD":
            counts["production: no canonical form for the unit"] += 1
            continue
        canonical = row["value"] * conversion.factor
        if not canonical or abs(canonical) > 1e11:
            counts["production: implausible magnitude, cleared"] += 1
            clear_scale(conn, row["id"])
            continue

        reserve = reserves.get((row["cik"], row["period_end"], row["product"] or ""))
        from_table = str(row["extracted_by"] or "").startswith("table:")
        if reserve:
            life = reserve / canonical
            if not (MIN_RESERVE_LIFE_YEARS <= life <= MAX_RESERVE_LIFE_YEARS):
                counts["production: rejected on reserve life"] += 1
                clear_scale(conn, row["id"])
                continue
            basis = (
                f"{life:,.0f} years of reserve life against this filer's own "
                f"proved reserves for the same product"
            )
        elif from_table:
            basis = "read from the printed table; unit from the column header"
        else:
            # Nothing to check the declaration against.
            counts["production: unit tagged in XBRL, uncorroborated"] += 1
            continue

        record_scale(
            conn, row["id"], 1.0, canonical, conversion.canonical, basis,
            conversion_note=conversion.note or None,
        )
        counts["production: resolved"] += 1


def resolve_direct(conn, counts) -> None:
    """Record the magnitude of facts that need no inference to read.

    Runs last and writes only where nothing is stored, so the reserve resolver
    stays the authority wherever it reached a verdict -- including the verdict
    that a row cannot be resolved and must be cleared.
    """
    rows = [
        dict(r)
        for r in conn.execute(
            f"""
            SELECT f.id, f.value, f.unit, f.extracted_by
            FROM fact_current f
            LEFT JOIN fact_scale s ON s.fact_id = f.id
            WHERE f.concept_key IN ({','.join('?' * len(DIRECT_CONCEPTS))})
              AND s.fact_id IS NULL
            """,
            DIRECT_CONCEPTS,
        )
    ]
    for row in rows:
        conversion = conversion_for(row["unit"])
        if conversion is None:
            # A rate unit, or a currency that is not USD. Both would need an
            # assumption this project makes explicitly or not at all: a heat
            # content for USD/Mcf, an exchange rate for CAD.
            counts["direct: no canonical form for the unit"] += 1
            continue
        from_table = str(row["extracted_by"] or "").startswith("table:")
        if conversion.canonical != "USD" and not from_table:
            counts["direct: volume tagged in XBRL, left to the reserve path"] += 1
            continue
        canonical = row["value"] * conversion.factor
        # The same ceiling the reserve path applies. Nothing in this cohort
        # produces 1e11 BOE in a year, so past that the column was misread.
        if conversion.canonical != "USD" and abs(canonical) > 1e11:
            counts["direct: implausible magnitude, cleared"] += 1
            clear_scale(conn, row["id"])
            continue
        record_scale(
            conn,
            row["id"],
            1.0,
            canonical,
            conversion.canonical,
            "read from the printed table; unit from the column header"
            if from_table else "as tagged; the value is already absolute",
            conversion_note=conversion.note or None,
        )
        counts["direct: resolved from the declaration"] += 1


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
                   f.product, f.extracted_by,
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

    counts: collections.Counter = collections.Counter()

    # Figures read off the printed table do not go through the inference at
    # all, and that is the whole point of reading them there.
    #
    # This resolver exists because a tagged value leaves two questions open:
    # what divisor the page applied, and which unit the filer meant. A table
    # reading answers both on the page — the divisor is 1 because the figure
    # IS what is printed, and the unit is the column header standing over it.
    # Putting such a row through the value-per-barrel test would let an
    # economic plausibility band overrule a fact that was read rather than
    # inferred, and would leave the cell blank whenever the filer happens not
    # to tag a standardized measure to test it against.
    table_rows = [r for r in rows if r["extracted_by"] == "table:reserves"]
    for row in table_rows:
        conversion = conversion_for(row["unit"])
        if conversion is None or conversion.canonical == "USD":
            counts["table: no canonical form for the unit"] += 1
            continue
        canonical = row["value"] * conversion.factor
        # The one check that still applies: nothing in this cohort holds 1e11
        # BOE. Past that the column header was misread, however clearly it
        # seemed to read.
        if abs(canonical) > 1e11:
            counts["table: implausible magnitude, cleared"] += 1
            clear_scale(conn, row["id"])
            continue
        record_scale(
            conn,
            row["id"],
            1.0,
            canonical,
            conversion.canonical,
            "read from the reserve table; unit from the column header",
            conversion_note=conversion.note or None,
        )
        counts["table: resolved from the printed unit"] += 1

    by_period: dict[tuple[str, str], list[dict]] = collections.defaultdict(list)
    for row in rows:
        if row["extracted_by"] == "table:reserves":
            continue
        by_period[(row["cik"], row["period_end"])].append(row)

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

    resolve_direct(conn, counts)
    # After the reserve resolver: reserve life is measured against what it
    # decided, so this cannot run before it has decided.
    resolve_production(conn, counts)
    conn.commit()

    total_facts = conn.execute("SELECT COUNT(*) FROM fact_scale").fetchone()[0]
    print(f"company-periods: " + ", ".join(f"{k} {v}" for k, v in counts.most_common()))
    print(f"facts with a canonical value: {total_facts}")
    conn.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
