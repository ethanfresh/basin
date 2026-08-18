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

from dataclasses import dataclass


@dataclass(frozen=True)
class ConceptSpec:
    """One logical fact, and the XBRL tags that may carry it."""

    key: str
    """Basin's stable name for the field. Never changes; tags may."""

    label: str
    """Human-readable description, for coverage reports."""

    aliases: tuple[tuple[str, str], ...]
    """``(taxonomy, tag)`` pairs, tried in order of expected reliability."""

    preferred_units: tuple[str, ...] = ()
    """Unit keys as ``companyfacts`` spells them, best first.

    Order is load-bearing, not documentation. A filer can tag the same
    quantity twice in different units -- Devon reports proved reserves as both
    MMBoe and MMcfe -- and both rows are legitimately storable. Something has
    to decide which one reaches the cell, and an arbitrary choice would make
    the panel non-reproducible. Empty means "accept any"; unlisted units sort
    after listed ones.
    """

    notes: str = ""

    def unit_rank(self, unit: str) -> int:
        """Position of *unit* in the preference order; unlisted units last."""
        try:
            return self.preferred_units.index(unit)
        except ValueError:
            return len(self.preferred_units)


# --- Product dimension -----------------------------------------------------
#
# XBRL tags these disclosures with a product axis (oil / gas / NGL), but the
# companyfacts API flattens dimensions away, so the axis member never arrives.
# What survives is the unit, and for some units that is enough to recover the
# product: a price in USD/bbl is an oil price, one in USD/Mcf is a gas price.
#
# Where the unit does NOT identify the product -- MBoe, MMBbls and friends can
# all mean barrels-of-oil-equivalent for the whole company -- product stays
# None rather than being guessed. A wrong product label is worse than a
# missing one: it silently mislabels a cell instead of leaving it visibly
# undimensioned.

_PRODUCT_BY_UNIT: dict[str, str] = {
    # Liquids prices
    "USD/bbl": "oil",
    "USD/Bbl": "oil",
    "USD/bbls": "oil",
    # Gas prices
    "USD/Mcf": "gas",
    "USD/MMBTU": "gas",
    "USD/MMBtu": "gas",
    "USD/Mmbtu": "gas",
    "USD/MMcf": "gas",
    # Gas volumes
    "Bcf": "gas",
    "MMcf": "gas",
    "Mcf": "gas",
}


def product_for_unit(unit: str) -> str | None:
    """Recover the product dimension from a unit, or None if it is ambiguous.

    BOE-style units are deliberately absent: they aggregate products, so
    labelling one 'oil' would be a fabrication.
    """
    return _PRODUCT_BY_UNIT.get(unit)


# --- Facts layer: taken from XBRL, exact, no language model involved --------

RESERVES_DEVELOPED = ConceptSpec(
    key="proved_developed_reserves_boe",
    label="Proved developed reserves (BOE)",
    aliases=(
        ("srt", "ProvedDevelopedReservesBOE1"),
        ("srt", "ProvedDevelopedReservesVolume"),
        ("us-gaap", "ProvedDevelopedReservesBOE1"),
        ("us-gaap", "ProvedDevelopedReservesVolume"),
        ("us-gaap", "ProvedDevelopedReservesBOE"),
    ),
    preferred_units=("MMBoe", "MBoe", "Boe", "boe", "MMBbls", "MBbls", "bbl", "MMcfe", "Bcf"),
)

RESERVES_UNDEVELOPED = ConceptSpec(
    key="proved_undeveloped_reserves_boe",
    label="Proved undeveloped reserves (BOE)",
    aliases=(
        ("srt", "ProvedUndevelopedReserveBOE1"),
        ("srt", "ProvedUndevelopedReserveBOE"),
        ("srt", "ProvedUndevelopedReserveVolume"),
        ("srt", "ProvedUndevelopedReservesVolume"),
        ("us-gaap", "ProvedUndevelopedReserveBOE1"),
        ("us-gaap", "ProvedUndevelopedReserveBOE"),
        ("us-gaap", "ProvedUndevelopedReserveVolume"),
    ),
    preferred_units=("MMBoe", "MBoe", "Boe", "boe", "MMBbls", "MBbls", "bbl", "MMcfe", "Bcf"),
)

RESERVES_TOTAL_PROVED = ConceptSpec(
    key="proved_reserves_boe",
    label="Total proved reserves (BOE)",
    aliases=(
        ("srt", "ProvedDevelopedAndUndevelopedReservesNet"),
        ("srt", "ProvedDevelopedAndUndevelopedReserveNetEnergy"),
        ("us-gaap", "ProvedDevelopedAndUndevelopedReservesNet"),
        ("us-gaap", "ProvedDevelopedAndUndevelopedReserveNetEnergy"),
    ),
    preferred_units=("MMBoe", "MBoe", "Boe", "boe", "MMBbls", "MBbls", "bbl", "MMcfe", "Bcf"),
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
        (
            "srt",
            "StandardizedMeasureOfDiscountedFutureNetCashFlowRelatingToProvedOilAndGasReserves",
        ),
        (
            "us-gaap",
            "StandardizedMeasureOfDiscountedFutureNetCashFlowRelatingToProvedOilAndGasReserves",
        ),
    ),
    preferred_units=("USD",),
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
    preferred_units=("MMBoe", "MBoe", "Boe", "boe", "MMBbls", "MBbls", "bbl", "MMcfe", "Bcf"),
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
    preferred_units=("USD",),
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
    preferred_units=("USD",),
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
    # Ranking only breaks ties *within* a product, since product partitions the
    # cell: a gas price tagged in both USD/Mcf and USD/MMBTU resolves to Mcf,
    # which is how realized gas prices are conventionally quoted.
    preferred_units=("USD/bbl", "USD/Bbl", "USD/Mcf", "USD/MMBTU", "USD/MMBtu"),
    notes="Sampled at 2/10. Regulation S-K 1200 requires the disclosure; "
    "most filers leave it untagged. Extraction layer owns this field.",
)

PRODUCTION_COST_PER_UNIT = ConceptSpec(
    key="production_cost_per_boe",
    label="Production cost (LOE) per unit of production",
    aliases=(
        ("srt", "ConsolidatedOilAndGasProductionCostsPerUnitOfProduction"),
        ("srt", "ProductionCostsPerUnitOfProduction"),
        ("srt", "AverageProductionCostsPerBarrelOfOilEquivalentsBOE"),
        ("us-gaap", "ConsolidatedOilAndGasProductionCostsPerUnitOfProduction"),
        ("us-gaap", "AverageProductionCostsPerBarrelOfOilEquivalentsBOE"),
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
