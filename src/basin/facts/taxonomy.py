"""Which accounting taxonomy a filer actually reports under.

This has to be measured, not inferred. The obvious proxy is domicile, and it is
wrong often enough to matter: of 23 foreign-domiciled companies in the cohort,
19 report under ``ifrs-full`` and 4 -- Gran Tierra, Imperial Oil, Indonesia
Energy, Tamboran -- report under ``us-gaap`` like any domestic filer. Reading
the country would have written off four filers Basin can reach.

The distinction is load-bearing rather than cosmetic, because the taxonomies do
not cover the same ground. Reserve and production disclosure is a US
requirement, so those concepts live in the SEC's ``srt`` namespace and have no
IFRS counterpart. An IFRS filer showing blank reserves is not a filer that
declined to tag them; it is one whose regulator never asked for them in
machine-readable form. Those two look identical in a coverage grid and mean
completely different things, which is why the taxonomy is recorded per company
and shown next to the gaps.

Taxonomy is not the whole story, and conflating it with comparability would be a
mistake. It answers "can the Facts layer read this filer" -- a question about
XBRL. A second, independent axis answers "do two extracted numbers mean the same
thing", and that one follows the SEC *form*, not the accounting standard:

  * **10-K and 20-F** -> Regulation S-K Subpart 1200. Form 20-F Item 4.D applies
    the same oil and gas disclosure regime a domestic 10-K uses, so Shell, BP,
    Equinor and Petrobras report proved reserves on SEC definitions despite
    reporting IFRS financials. Checked against the filings: "proved reserves"
    appears 73, 112, 11 and 44 times in their current 20-Fs.
  * **40-F** -> Canadian NI 51-101, via the MJDS. Different definitions, not
    merely different formatting: reserves are evaluated at *forecast* prices
    rather than the SEC's trailing 12-month average, the headline number is
    usually 2P (proved **plus probable**) where the SEC's is proved alone, and
    values are quoted before tax at several discount rates instead of the single
    after-tax 10% standardized measure. Canadian Natural's 40-F carries
    "NI 51-101" 5 times, "probable reserves" 17 and "forecast prices" 16.

So Shell is IFRS *and* comparable; Cenovus is IFRS *and* not. Only the second is
a comparability problem, and a single field could not have said so.
"""

from __future__ import annotations

# Namespaces that carry reportable financial facts. dei is entity metadata,
# ffd and ecd are cover-page and compensation disclosures, invest is a schedule
# -- none of them say anything about which standards a filer reports under.
_ACCOUNTING_NAMESPACES = ("us-gaap", "ifrs-full")

IFRS = "ifrs-full"
US_GAAP = "us-gaap"
UNKNOWN = "unknown"

# Reserve disclosure regimes, keyed by the annual form that carries them.
SUBPART_1200 = "subpart-1200"   # SEC definitions: 10-K and 20-F
NI_51_101 = "ni-51-101"         # Canadian MJDS: 40-F

_REGIME_BY_FORM = {
    "10-K": SUBPART_1200,
    "20-F": SUBPART_1200,
    "40-F": NI_51_101,
}

ANNUAL_FORMS = tuple(_REGIME_BY_FORM)


def detect_reporting_taxonomy(payload: dict | None) -> tuple[str, str | None]:
    """Return ``(taxonomy, note)`` for one ``companyfacts`` payload.

    Decided by which accounting namespace carries more tags, because a filer can
    hold a handful of the other one: Petrobras reports 397 ifrs-full tags and
    266 us-gaap, and Cenovus reports 288 ifrs-full against a single us-gaap tag.
    The minority namespace is recorded in the note rather than discarded, since
    a genuinely mixed filer is worth being able to find later.
    """
    if not payload:
        return UNKNOWN, "no companyfacts payload"

    facts = payload.get("facts") or {}
    counts = {ns: len(facts.get(ns) or {}) for ns in _ACCOUNTING_NAMESPACES}
    counts = {ns: n for ns, n in counts.items() if n}

    if not counts:
        return UNKNOWN, "no us-gaap or ifrs-full facts"

    winner = max(counts, key=lambda ns: counts[ns])
    note = ", ".join(f"{ns}:{n}" for ns, n in sorted(counts.items()))
    return winner, note


def reaches_reserves(taxonomy: str) -> bool:
    """Whether the Facts layer can reach reserve concepts for this taxonomy.

    False for IFRS is a statement about the taxonomy, not about the filer: the
    disclosure is still in the 20-F, it is simply not tagged. That makes it
    extraction-layer work rather than a coverage failure.
    """
    return taxonomy == US_GAAP


def annual_form(forms: list[str] | tuple[str, ...]) -> str | None:
    """The annual report form this filer uses, from its filing history.

    Amendments (``10-K/A``, ``20-F/A``) count -- they are the same regime -- and
    a filer that has migrated between forms is reported by whichever it has
    filed most, since the regime that matters is the one currently in force.
    """
    counts: dict[str, int] = {}
    for form in forms:
        base = form.split("/")[0]
        if base in _REGIME_BY_FORM:
            counts[base] = counts.get(base, 0) + 1
    if not counts:
        return None
    return max(counts, key=lambda f: counts[f])


def detect_disclosure_regime(forms: list[str] | tuple[str, ...]) -> tuple[str | None, str | None]:
    """Return ``(regime, note)`` for a filer, from the annual forms it files.

    Derived from the form rather than from domicile or taxonomy because the form
    is what the requirement attaches to. A Canadian issuer that files a 10-K is
    on SEC definitions; one that files a 40-F is not, and both are common.
    """
    form = annual_form(forms)
    if form is None:
        return None, "no annual report form filed"
    regime = _REGIME_BY_FORM[form]
    if regime == NI_51_101:
        return regime, (
            f"{form} under MJDS -- reserves on Canadian NI 51-101 definitions "
            "(forecast prices, 2P headline, pre-tax values); not directly "
            "comparable to SEC proved reserves"
        )
    return regime, f"{form} -- reserves on SEC Regulation S-K Subpart 1200 definitions"


def is_comparable_to_sec(regime: str | None) -> bool:
    """Whether reserve figures under *regime* sit in an SEC-defined column.

    NI 51-101 figures are real and useful; they simply are not the same
    measurement. Putting a 2P forecast-price number in a column of SEC proved
    reserves is the single most misleading cell a peer table could hold, so the
    regime travels with the value rather than being resolved away.
    """
    return regime == SUBPART_1200
