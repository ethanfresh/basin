"""Convert a filing's declared unit to a canonical one.

Two conversions happen before figures from different filers can share a
column, and they are different in kind:

* **Scale** — whether ``2,521,028,000`` is presented as ``2,521,028``. This is
  measured, not assumed: it comes from finding the value in the document
  (:mod:`basin.documents.verify`). Nothing here guesses it.
* **Unit** — MBoe to BOE, Bcfe to BOE. These factors are definitional except
  one, and that one is flagged rather than hidden.

The exception is gas. Converting a gas volume to barrels of oil equivalent
uses 6 Mcf = 1 BOE, which is a *convention* rather than a physical identity --
it is the ratio SEC Regulation S-K Subpart 1200 defines for reporting, and the
one the industry quotes, but a barrel of oil and six Mcf of gas have neither
the same energy content nor remotely the same price. A panel that silently
mixes gas-converted and oil-native figures is making an economic claim, so
cells that needed the convention say so.
"""

from __future__ import annotations

from dataclasses import dataclass

# One barrel of oil equivalent, expressed in cubic feet of gas.
MCF_PER_BOE = 6.0
CUBIC_FEET_PER_BOE = MCF_PER_BOE * 1_000

BOE = "BOE"
USD = "USD"

GAS_CONVERSION_NOTE = f"gas converted at {MCF_PER_BOE:.0f} Mcf = 1 BOE (Reg S-K 1200)"


@dataclass(frozen=True)
class Conversion:
    """How to get from a declared unit to a canonical one."""

    factor: float
    canonical: str
    note: str = ""

    @property
    def is_convention(self) -> bool:
        return bool(self.note)


# Liquid and equivalent volumes. A "barrel" and a "barrel of oil equivalent"
# are the same size, so these are pure scale factors.
_VOLUME: dict[str, float] = {
    "boe": 1.0, "Boe": 1.0, "BOE": 1.0,
    "bbl": 1.0, "bbls": 1.0, "Bbl": 1.0, "Bbls": 1.0, "BBL": 1.0,
    "MBoe": 1e3, "MBbl": 1e3, "MBbls": 1e3, "Mbbls": 1e3,
    "MMBoe": 1e6, "MMBbl": 1e6, "MMBbls": 1e6, "MMbbls": 1e6,
}

# Gas volumes, in cubic feet, before the 6:1 convention is applied.
_GAS_CUBIC_FEET: dict[str, float] = {
    "ft3": 1.0,
    "Mcf": 1e3, "Mcfe": 1e3,
    "MMcf": 1e6, "MMcfe": 1e6,
    "Bcf": 1e9, "Bcfe": 1e9,
    "Tcf": 1e12, "Tcfe": 1e12,
}

_MONEY = {"USD", "usd"}


def conversion_for(unit: str) -> Conversion | None:
    """Factor taking one *unit* to its canonical form, or None if it has none.

    Returns None for rate units such as ``USD/bbl``: a realized price is
    already per-unit, and forcing it into a common denominator would require
    the very heat-content assumption this module refuses to make silently.
    """
    if unit in _MONEY:
        return Conversion(1.0, USD)
    if unit in _VOLUME:
        return Conversion(_VOLUME[unit], BOE)
    if unit in _GAS_CUBIC_FEET:
        return Conversion(
            _GAS_CUBIC_FEET[unit] / CUBIC_FEET_PER_BOE, BOE, GAS_CONVERSION_NOTE
        )
    return None


def normalise(value: float, unit: str, scale: float = 1.0) -> tuple[float, str, str] | None:
    """Return ``(canonical_value, canonical_unit, note)``.

    ``scale`` is the factor the document was found to print the figure at, so
    ``value / scale`` is the figure as the filing states it. Returns None when
    the unit has no canonical form.
    """
    conversion = conversion_for(unit)
    if conversion is None:
        return None
    printed = value / (scale or 1.0)
    return printed * conversion.factor, conversion.canonical, conversion.note
