"""Read *dimensioned* facts from a filing's XBRL instance document.

The ``companyfacts`` API drops every dimension, which is why a whole class of
disclosure looks absent from it. Oil, gas and NGL reserves are the clearest
case: filers report them separately, the API returns only the undimensioned
roll-up, and the product split simply is not there. A sweep of all 94 cached
payloads found no reserve *volume* tag that names a product, and only two or
three filers where the split survives at all -- and there only by accident,
because the two components happen to carry different units.

The instance document, filed alongside the human-readable document in the same
submission, keeps the dimensions. Diamondback's FY2025 reserves resolve to
1,774,420 MBbls of oil, 964,466 MBbls of NGL and 5,273,821 MMcf of gas, which
reconcile to the 3,617,856 MBoe total already in the store.

Facts point at a context by id; the context carries the period and the axis
members. So the parse is: read contexts, read units, then read facts and join.
Regexes rather than an XML parser because these documents run to several
megabytes and only a handful of tags are wanted -- and because the SEC's own
formatting puts attributes on separate lines, which is worth stating since
assuming otherwise silently returned zero dimensions the first time.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

_CONTEXT = re.compile(
    r'(?is)<(?:\w+:)?context\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</(?:\w+:)?context>'
)
_MEMBER = re.compile(
    r'(?is)<(?:\w+:)?explicitMember\b[^>]*\bdimension="([^"]+)"[^>]*>\s*([^<\s]+)\s*<'
)
_PERIOD_END = re.compile(r"(?is)<(?:\w+:)?(?:instant|endDate)>\s*([^<\s]+)\s*<")
_PERIOD_START = re.compile(r"(?is)<(?:\w+:)?startDate>\s*([^<\s]+)\s*<")
_UNIT = re.compile(
    r'(?is)<(?:\w+:)?unit\b[^>]*\bid="([^"]+)"[^>]*>(.*?)</(?:\w+:)?unit>'
)
_MEASURE = re.compile(r"(?is)<(?:\w+:)?measure>\s*([^<\s]+)\s*<")

# Axes on which a member describes *what the fact measures*. Found by
# sweeping 143 inline documents rather than assumed -- the reserve tables hang
# off ReserveQuantitiesByTypeOfReserveAxis, which no amount of guessing would
# have produced.
#
# DerivativeInstrumentRiskAxis and TradingActivityByTypeAxis also carry
# NaturalGas members and are deliberately excluded: there the member says what
# a hedge is written against, not what the volume is. Treating those as a
# product would file swap notionals as gas reserves.
_PRODUCT_AXES = (
    "ReserveQuantitiesByTypeOfReserveAxis",
    "ProductOrServiceAxis",
    "EnergyAxis",
    "OilAndGasDeliveryCommitmentsAndContractsAxis",
    "OilAndGasDeliveryAxis",
)

# Axis members mapped to Basin's product vocabulary. Anything unrecognised
# keeps its raw member name rather than being forced into a bucket.
_PRODUCT_BY_MEMBER = {
    # Oil
    "CrudeOilMember": "oil",
    "OilReservesMember": "oil",
    "OilMember": "oil",
    "OilAndCondensateMember": "oil",
    "CrudeOilAndCondensateMember": "oil",
    # Gas
    "NaturalGasReservesMember": "gas",
    "NaturalGasMember": "gas",
    "NaturalGasProductionMember": "gas",
    # Natural gas liquids
    "NaturalGasLiquidsReservesMember": "ngl",
    "NaturalGasLiquidsMember": "ngl",
    "NglMember": "ngl",
    "NaturalGasLiquidsProductionMember": "ngl",
}

# Members that name a *combination* rather than a product. Mapping these to
# one product would double-count against the components, so they are
# recognised and skipped rather than silently falling through as unlabelled.
_COMBINED_MEMBERS = {
    "OilAndNaturalGasMember",
    "OilAndGasMember",
    "CrudeOilAndNGLMember",
    "OilAndCondensateAndNglMember",
}


@dataclass(frozen=True)
class DimensionedFact:
    """One fact, with the product dimension the API would have dropped."""

    tag: str
    taxonomy: str
    value: float
    unit: str
    period_end: str
    period_start: str | None
    product: str | None
    member: str | None
    context: str

    @property
    def is_total(self) -> bool:
        """True when the fact carries no product dimension at all."""
        return self.member is None


def parse_contexts(raw: str) -> dict[str, tuple[str | None, str | None, dict[str, str]]]:
    """``context id -> (period_end, period_start, {axis: member})``."""
    out: dict[str, tuple[str | None, str | None, dict[str, str]]] = {}
    for match in _CONTEXT.finditer(raw):
        body = match.group(2)
        end = _PERIOD_END.search(body)
        start = _PERIOD_START.search(body)
        out[match.group(1)] = (
            end.group(1) if end else None,
            start.group(1) if start else None,
            dict(_MEMBER.findall(body)),
        )
    return out


def parse_units(raw: str) -> dict[str, str]:
    """``unit id -> measure``, with the namespace prefix dropped."""
    out: dict[str, str] = {}
    for match in _UNIT.finditer(raw):
        measures = _MEASURE.findall(match.group(2))
        out[match.group(1)] = "/".join(m.split(":")[-1] for m in measures)
    return out


def product_of(dimensions: dict[str, str]) -> tuple[str | None, str | None]:
    """``(product, raw member)`` for whichever axis carries the product."""
    for axis, member in dimensions.items():
        if any(name in axis for name in _PRODUCT_AXES):
            short = member.split(":")[-1]
            if short in _COMBINED_MEMBERS:
                return None, short
            return _PRODUCT_BY_MEMBER.get(short), short
    return None, None


def facts_for_tags(raw: str, tags: tuple[str, ...]) -> list[DimensionedFact]:
    """Every fact for *tags*, with its period, unit and product resolved."""
    contexts = parse_contexts(raw)
    units = parse_units(raw)

    out: list[DimensionedFact] = []
    for qualified in tags:
        taxonomy, _, tag = qualified.partition(":")
        pattern = re.compile(
            rf"(?is)<{re.escape(qualified)}\b([^>]*)>\s*([^<]*)\s*</{re.escape(qualified)}>"
        )
        for match in pattern.finditer(raw):
            attrs, text = match.group(1), match.group(2).strip()
            if not text:
                continue
            context_ref = re.search(r'contextRef="([^"]+)"', attrs)
            unit_ref = re.search(r'unitRef="([^"]+)"', attrs)
            if context_ref is None:
                continue
            end, start, dimensions = contexts.get(context_ref.group(1), (None, None, {}))
            if end is None:
                continue
            product, member = product_of(dimensions)
            try:
                value = float(text.replace(",", ""))
            except ValueError:
                continue
            out.append(
                DimensionedFact(
                    tag=tag,
                    taxonomy=taxonomy,
                    value=value,
                    unit=units.get(unit_ref.group(1) if unit_ref else "", ""),
                    period_end=end,
                    period_start=start,
                    product=product,
                    member=member,
                    context=context_ref.group(1),
                )
            )
    return out


_IX_FACT = re.compile(r"(?is)<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>")
_ATTR = re.compile(r'(\w[\w:-]*)="([^"]*)"')
_TAGS_INSIDE = re.compile(r"<[^>]+>")


def inline_facts(raw: str, tags: tuple[str, ...]) -> list[DimensionedFact]:
    """Facts read from the *inline* XBRL in a primary document.

    The human-readable filing is itself the XBRL instance: every tagged figure
    sits in an ``<ix:nonFraction>`` carrying its concept, context, unit and --
    crucially -- its ``scale``. The displayed "1,069,508" with ``scale="3"``
    is the same fact companyfacts serves as 1,069,508,000.

    That makes two inferences unnecessary. The presentation scale is stated
    rather than deduced, and the product dimension arrives through the context
    instead of being guessed from a unit. The unit itself stays untrustworthy:
    Gulfport tags 3,612 Bcf of gas as ``unit="bbl"``.
    """
    contexts = parse_contexts(raw)
    units = parse_units(raw)
    wanted = set(tags)

    out: list[DimensionedFact] = []
    for match in _IX_FACT.finditer(raw):
        attrs = dict(_ATTR.findall(match.group(1)))
        name = attrs.get("name", "")
        if name not in wanted:
            continue
        shown = _TAGS_INSIDE.sub("", match.group(2)).replace(",", "").strip()
        if not shown or shown in {"-", "—"}:
            continue
        try:
            value = float(shown) * (10 ** int(attrs.get("scale", "0")))
        except ValueError:
            continue
        if attrs.get("sign") == "-":
            value = -value
        end, start, dimensions = contexts.get(attrs.get("contextRef", ""), (None, None, {}))
        if end is None:
            continue
        product, member = product_of(dimensions)
        taxonomy, _, tag = name.partition(":")
        out.append(
            DimensionedFact(
                tag=tag,
                taxonomy=taxonomy,
                value=value,
                unit=units.get(attrs.get("unitRef", ""), ""),
                period_end=end,
                period_start=start,
                product=product,
                member=member,
                context=attrs.get("contextRef", ""),
            )
        )
    return out


def instance_name(primary_document: str) -> str:
    """The instance document that sits beside a primary document.

    EDGAR names it after the primary: ``fang-20251231.htm`` is accompanied by
    ``fang-20251231_htm.xml``.
    """
    stem = primary_document.rsplit(".", 1)[0]
    return f"{stem}_htm.xml"
