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
# What survives is the unit -- and the unit only recovers the product for
# *rates*. A price quoted in USD/bbl is an oil price and one in USD/Mcf is a
# gas price, because the denominator names the thing being priced.
#
# Volumes are the opposite, and inferring from them was a mistake worth
# spelling out. A reserve figure in Boe, MBoe, Bcfe or Mcfe is an *aggregate*
# across oil, gas and NGL -- "equivalent" is what the e means -- so no product
# label is truthful. Worse, a bare gas unit on an aggregate concept is usually
# a filer omitting the e rather than reporting gas alone: EQT states total
# proved reserves in MMcf and EOG in Bcf, and neither company is gas-only.
#
# On EOG that inference did real damage. companyfacts returned total proved
# reserves under one tag in two units, 1,548 MMBbls and 8,222 Bcf, which are
# the flattened oil and gas *components* -- EOG's actual total is about 3,750
# MMBoe. Labelling the Bcf row "gas" made it a separate cell, so the panel
# showed EOG twice and neither row was the total. The product dimension exists
# to stop cells colliding; guessing it from a volume unit manufactured a
# collision instead.

_PRODUCT_BY_UNIT: dict[str, str] = {
    # Liquids prices -- the denominator names the product.
    "USD/bbl": "oil",
    "USD/Bbl": "oil",
    "USD/bbls": "oil",
    # Gas prices.
    "USD/Mcf": "gas",
    "USD/MMBTU": "gas",
    "USD/MMBtu": "gas",
    "USD/Mmbtu": "gas",
    "USD/MMcf": "gas",
    "USD/Mcfe": "gas",
    "USD/MMcfe": "gas",
}


def product_for_unit(unit: str) -> str | None:
    """Recover the product from a unit, or None where it cannot be known.

    Only rate units qualify. Volume units are deliberately absent: BOE- and
    equivalent-denominated figures aggregate products, and a bare gas unit on
    an aggregate concept usually means the filer dropped the "e".
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
        # IFRS filers -- 19 of the 23 foreign-domiciled cohort members report
        # under ifrs-full rather than us-gaap. The product-specific tags come
        # first for the same reason as above: bare Revenue is the whole company.
        ("ifrs-full", "RevenueFromSaleOfOilAndGasProducts"),
        ("ifrs-full", "RevenueFromSaleOfCrudeOil"),
        ("ifrs-full", "RevenueFromSaleOfNaturalGas"),
        ("ifrs-full", "RevenueFromContractsWithCustomers"),
        ("ifrs-full", "Revenue"),
    ),
    preferred_units=("USD",),
    notes="The contract-with-customer tag is last on purpose: it is total "
    "revenue, not oil and gas revenue, so it is a fallback and the basis "
    "difference must reach the cell. The same caveat applies to ifrs-full "
    "Revenue, and more sharply for an integrated filer whose revenue is "
    "mostly refined product.",
)

CAPEX = ConceptSpec(
    key="capex",
    label="Capital expenditure (actual)",
    aliases=(
        ("us-gaap", "PaymentsToAcquireOilAndGasProperty"),
        ("us-gaap", "PaymentsToExploreAndDevelopOilAndGasProperties"),
        ("us-gaap", "PaymentsToAcquirePropertyPlantAndEquipment"),
        ("ifrs-full", "PurchaseOfOilAndGasAssets"),
        ("ifrs-full", "PurchaseOfPropertyPlantAndEquipmentClassifiedAsInvestingActivities"),
        ("ifrs-full", "AdditionsOtherThanThroughBusinessCombinationsPropertyPlantAndEquipment"),
    ),
    preferred_units=("USD",),
    notes="Guided capex is not tagged anywhere; it comes from 8-K EX-99.1.",
)


# --- What IFRS does not carry, and the trap in looking for it --------------
#
# The reserve, production and standardized-measure concepts above have no
# ifrs-full equivalent, and this is not an oversight in the registry. Reserve
# disclosure is a US requirement -- Regulation S-K Subpart 1200 and ASC 932 --
# so the concepts live in the SEC's own ``srt`` namespace. IFRS has no
# hydrocarbon reserve taxonomy at all. A sweep of every ifrs-full tag across the
# foreign-domiciled cohort found exploration expense, oil and gas revenue and
# asset purchases, and nothing describing a reserve base or a production volume.
#
# The trap: ifrs-full *does* define ``OtherReserves`` and
# ``ReserveOfExchangeDifferencesOnTranslation``, tagged by 9 and 4 of these
# filers. Those are equity reserves -- retained amounts on the balance sheet --
# and have nothing to do with hydrocarbons. A name-matched alias would populate
# a reserves column with shareholders' equity, in the right units, looking
# entirely plausible. They are deliberately absent from the registry.
#
# So for an IFRS filer the Facts layer reaches revenue and capex, and reserves
# and production are extraction-layer work against the 20-F text.


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
