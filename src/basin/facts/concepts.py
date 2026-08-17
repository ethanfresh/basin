"""The XBRL concepts Basin reads, and the aliases each one hides behind.

The feasibility sampling found that filers tag the same disclosure under
different taxonomies — some use ``srt:``, some the ``us-gaap:`` variant — so a
flat concept list silently under-reports coverage. Each :class:`ConceptSpec`
therefore names one *logical* field and every taxonomy/tag pair known to carry
it. Lookup tries them in order and reports which alias actually hit, because
"which tag did this company use" is itself a finding worth keeping.

Only Facts-layer fields belong here. Realized price per unit, LOE per BOE, and
cash G&A per BOE are listed as *known gaps*: the sampling showed they are
tagged too rarely to depend on, so they are the extraction layer's job. They
are recorded here anyway so the coverage report can quantify the gap rather
than assume it.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass(frozen=True)
class ConceptSpec:
    """One logical fact, and the XBRL tags that may carry it."""

    key: str
    """Basin's stable name for the field. Never changes; tags may."""

    label: str
    """Human-readable description, for coverage reports."""

    aliases: tuple[tuple[str, str], ...]
    """``(taxonomy, tag)`` pairs, tried in order of expected reliability."""

    expected_units: tuple[str, ...] = ()
    """Unit keys as ``companyfacts`` spells them. Empty means "accept any"."""

    notes: str = ""


# --- Facts layer: taken from XBRL, exact, no language model involved --------

RESERVES_DEVELOPED = ConceptSpec(
    key="proved_developed_reserves_boe",
    label="Proved developed reserves (BOE)",
    aliases=(
        ("srt", "ProvedDevelopedReservesBOE1"),
        ("srt", "ProvedDevelopedReservesVolume"),
        ("us-gaap", "ProvedDevelopedReservesBOE1"),
    ),
    expected_units=("boe", "bbl", "MBoe", "MMBoe"),
)

RESERVES_UNDEVELOPED = ConceptSpec(
    key="proved_undeveloped_reserves_boe",
    label="Proved undeveloped reserves (BOE)",
    aliases=(
        ("srt", "ProvedUndevelopedReserveBOE"),
        ("srt", "ProvedUndevelopedReservesVolume"),
        ("us-gaap", "ProvedUndevelopedReserveBOE"),
    ),
    expected_units=("boe", "bbl", "MBoe", "MMBoe"),
)

RESERVES_TOTAL_PROVED = ConceptSpec(
    key="proved_reserves_boe",
    label="Total proved reserves (BOE)",
    aliases=(
        ("srt", "ProvedDevelopedAndUndevelopedReservesNet"),
        ("srt", "ProvedDevelopedAndUndevelopedReserveNetEnergy"),
        ("us-gaap", "ProvedDevelopedAndUndevelopedReservesNet"),
    ),
    expected_units=("boe", "bbl", "MBoe", "MMBoe"),
)

STANDARDIZED_MEASURE = ConceptSpec(
    key="standardized_measure",
    label="Standardized measure of discounted future net cash flows",
    aliases=(
        (
            "srt",
            "StandardizedMeasureOfDiscountedFutureNetCashFlowsRelatingToProvedOilAndGasReserves",
        ),
        (
            "us-gaap",
            "StandardizedMeasureOfDiscountedFutureNetCashFlowsRelatingToProvedOilAndGasReserves",
        ),
    ),
    expected_units=("USD",),
    notes="PV-10 is a non-GAAP cousin of this and is usually untagged.",
)

PRODUCTION_VOLUME = ConceptSpec(
    key="production_volume",
    label="Annual production volume",
    aliases=(
        ("srt", "ProvedDevelopedAndUndevelopedReserveProductionEnergy"),
        ("srt", "ProvedDevelopedAndUndevelopedReserveProduction"),
        ("us-gaap", "ProvedDevelopedAndUndevelopedReserveProductionEnergy"),
    ),
    expected_units=("boe", "bbl", "MBoe", "MMBoe"),
    notes="Reported as the production line of the reserve rollforward.",
)

OIL_AND_GAS_REVENUE = ConceptSpec(
    key="oil_and_gas_revenue",
    label="Oil and gas revenue",
    aliases=(
        ("us-gaap", "OilAndGasRevenue"),
        ("us-gaap", "OilAndGasSalesRevenue"),
        ("us-gaap", "RevenueFromContractWithCustomerExcludingAssessedTax"),
    ),
    expected_units=("USD",),
    notes="The contract-with-customer tag is last on purpose: it is total "
    "revenue, not oil and gas revenue, so it is a fallback and the basis "
    "difference must reach the cell.",
)

CAPEX = ConceptSpec(
    key="capex",
    label="Capital expenditure (actual)",
    aliases=(
        ("us-gaap", "PaymentsToAcquireOilAndGasProperty"),
        ("us-gaap", "PaymentsToExploreAndDevelopOilAndGasProperties"),
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
    ),
    expected_units=("USD",),
    notes="Guided capex is not tagged anywhere; it comes from 8-K EX-99.1.",
)


# --- Known gaps: required disclosures the sampling found rarely tagged -----
#
# Kept in the registry so the coverage report measures the gap instead of
# assuming it. Whatever these do not cover is the extraction layer's mandate.

AVERAGE_SALES_PRICE = ConceptSpec(
    key="average_sales_price",
    label="Average realized sales price per unit",
    aliases=(
        ("srt", "AverageSalesPrices"),
        ("srt", "AverageSalePricePerUnitOfProduction"),
        ("us-gaap", "AverageSalesPrices"),
    ),
    notes="Sampled at 2/10. Regulation S-K 1200 requires the disclosure; "
    "most filers leave it untagged. Extraction layer owns this field.",
)

PRODUCTION_COST_PER_UNIT = ConceptSpec(
    key="production_cost_per_boe",
    label="Production cost (LOE) per unit of production",
    aliases=(
        ("srt", "ConsolidatedOilAndGasProductionCostsPerUnitOfProduction"),
        ("srt", "ProductionCostsPerUnitOfProduction"),
        ("us-gaap", "ConsolidatedOilAndGasProductionCostsPerUnitOfProduction"),
    ),
    notes="Sampled at 1/10. Extraction layer owns this field.",
)


FACTS_LAYER_CONCEPTS: tuple[ConceptSpec, ...] = (
    RESERVES_DEVELOPED,
    RESERVES_UNDEVELOPED,
    RESERVES_TOTAL_PROVED,
    STANDARDIZED_MEASURE,
    PRODUCTION_VOLUME,
    OIL_AND_GAS_REVENUE,
    CAPEX,
)

KNOWN_GAP_CONCEPTS: tuple[ConceptSpec, ...] = (
    AVERAGE_SALES_PRICE,
    PRODUCTION_COST_PER_UNIT,
)

ALL_CONCEPTS: tuple[ConceptSpec, ...] = FACTS_LAYER_CONCEPTS + KNOWN_GAP_CONCEPTS

BY_KEY: dict[str, ConceptSpec] = {spec.key: spec for spec in ALL_CONCEPTS}


def spec(key: str) -> ConceptSpec:
    """Look up a concept by Basin's stable key."""
    try:
        return BY_KEY[key]
    except KeyError:
        raise KeyError(
            f"unknown concept {key!r}; known: {sorted(BY_KEY)}"
        ) from None
