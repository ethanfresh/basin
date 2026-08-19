"""Locate a stored fact by the filing's own markup.

A primary document is inline XBRL: every tagged figure sits inside an
``<ix:nonFraction>`` that names its concept, points at a context carrying the
period and any dimensions, names its unit, and states its presentation scale.

    <ix:nonFraction unitRef="mbbls" contextRef="c-610" scale="3"
      name="srt:ProvedDevelopedAndUndevelopedReservesNet" id="f-1841">1,069,508</ix:nonFraction>

Searching the text for "1,069,508" finds that number and several others like
it; reading the element finds *this fact*. Across the store only 230 of 669
string matches were unique, so two thirds of citations were pointing at one of
several identical-looking numbers, chosen by document order (defect D1).

The element also states ``scale``, which the resolver previously had to infer
from an economic identity (defect D3). Reading it removes the inference for
every tagged fact.

What it does not fix is the unit. ``unitRef`` is filer-declared and sometimes
wrong in the markup itself -- Gulfport tags 3,612 Bcf of gas as ``bbl`` -- so
unit correction stays exactly where it is.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

from basin.facts.instance import parse_contexts, parse_units, product_of

_IX = re.compile(r"(?is)<ix:nonFraction\b([^>]*)>(.*?)</ix:nonFraction>")
_ATTR = re.compile(r'(\w[\w:-]*)="([^"]*)"')
_INNER_TAGS = re.compile(r"<[^>]+>")


def half_unit_last_place(shown: str, scale: int) -> float | None:
    """Half a unit in the last place *shown* prints, expressed in scaled units.

    "3,613" prints to the unit, so at scale 6 it stands for anything within
    500,000 of 3,613,000,000. "2.5" prints to a tenth, so at scale 9 it covers
    50,000,000. This is the whole of the difference rounding can produce, and
    therefore the whole of the difference a genuine match may have.
    """
    digits = shown.strip().replace(",", "").replace("(", "").replace(")", "").lstrip("-")
    if not digits or not digits.replace(".", "").isdigit():
        return None
    decimals = len(digits.split(".")[1]) if "." in digits else 0
    return 0.5 * (10 ** -decimals) * (10 ** scale)


@dataclass(frozen=True)
class TaggedFigure:
    """One figure as the filing itself marks it up."""

    concept: str
    """Qualified name, e.g. ``srt:ProvedDevelopedReservesBOE1``."""

    shown: str
    """The figure as printed, before scale is applied."""

    value: float
    """``shown`` scaled — the value companyfacts serves."""

    scale: int
    unit: str
    product: str | None
    member: str | None
    period_end: str
    period_start: str | None
    element_id: str | None
    start: int
    end: int

    @property
    def anchor(self) -> str | None:
        """Fragment addressing this figure in the filing, e.g. ``#f-1841``."""
        return f"#{self.element_id}" if self.element_id else None


def tagged_figures(raw: str) -> list[TaggedFigure]:
    """Every inline-XBRL figure in a document, with its position."""
    contexts = parse_contexts(raw)
    units = parse_units(raw)

    out: list[TaggedFigure] = []
    for match in _IX.finditer(raw):
        attrs = dict(_ATTR.findall(match.group(1)))
        shown = _INNER_TAGS.sub("", match.group(2)).strip()
        cleaned = shown.replace(",", "").replace(" ", "").strip()
        if not cleaned or cleaned in {"-", "—"}:
            continue
        try:
            scale = int(attrs.get("scale", "0"))
            value = float(cleaned) * (10**scale)
        except ValueError:
            continue
        if attrs.get("sign") == "-":
            value = -value

        end, start, dimensions = contexts.get(
            attrs.get("contextRef", ""), (None, None, {})
        )
        if end is None:
            continue
        product, member = product_of(dimensions)
        out.append(
            TaggedFigure(
                concept=attrs.get("name", ""),
                shown=shown,
                value=value,
                scale=scale,
                unit=units.get(attrs.get("unitRef", ""), ""),
                product=product,
                member=member,
                period_end=end,
                period_start=start,
                element_id=attrs.get("id"),
                start=match.start(),
                end=match.end(),
            )
        )
    return out


def match_fact(
    figures: list[TaggedFigure],
    *,
    concept_tag: str | None,
    period_end: str,
    value: float,
    product: str | None = None,
    tolerance: float | None = None,
) -> TaggedFigure | None:
    """Find the figure a stored fact came from.

    Matched on identity — concept, period, product and value — rather than on
    the printed string, so a number that appears five times in the filing still
    resolves to the one occurrence that carries this fact.

    Preference runs from most to least specific: an exact concept and period
    match beats a value-only match, and among equals the earliest occurrence
    wins so the result is stable.
    """
    def close(figure: TaggedFigure, target: float) -> bool:
        """Whether *figure* can be the markup of *target*.

        The only difference these two may legitimately have is the rounding the
        filing applied when it printed the number, so the allowance comes from
        the printed precision rather than from a flat percentage. A figure shown
        as "3,613" at scale 6 stands for 3,613,000,000 give or take half of the
        last printed place -- 500,000 -- and nothing else.

        A flat 0.5% was far too loose at this magnitude. Diamondback's proved
        reserves are 3,617,856 MBoe; the filing's capitalized-costs table
        carries 3,613 at scale 6, which differs by 0.134% and was accepted.
        Verification then recorded a printed scale of 1e6 taken from an
        unrelated figure, and the scale resolver -- correctly, for that input --
        could not resolve the reserves at all.
        """
        if figure.value == target:
            return True
        allowance = half_unit_last_place(figure.shown, figure.scale)
        if allowance is None:
            # No printed form to reason from; fall back to a tight relative
            # tolerance rather than the old permissive one.
            scale = max(abs(figure.value), abs(target))
            return scale > 0 and abs(figure.value - target) / scale <= 5e-4
        return abs(figure.value - target) <= allowance

    ranked: list[tuple[tuple[int, int, int], TaggedFigure]] = []
    for figure in figures:
        if figure.period_end != period_end:
            continue
        if not close(figure, value):
            continue
        concept_hit = bool(concept_tag) and figure.concept.split(":")[-1] == concept_tag
        product_hit = (figure.product or None) == (product or None)
        # Lower sorts first: concept match, then product match, then position.
        ranked.append(((0 if concept_hit else 1, 0 if product_hit else 1, figure.start), figure))

    if not ranked:
        return None
    ranked.sort(key=lambda pair: pair[0])
    # A value that matches nothing about the concept is not evidence.
    best_key, best = ranked[0]
    if concept_tag and best_key[0] == 1:
        return None
    return best
