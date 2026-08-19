"""Cohort assignment from the SEC's own SIC classification.

A cohort decides what a company may be compared against, so where it comes from
is a load-bearing decision. It used to come from the Finviz Elite screener,
which classified better than SIC but is a paid, licensed feed: its terms do not
permit redistributing the classification inside a product, and it put the one
non-public dependency in an otherwise entirely public pipeline. EDGAR assigns
every filer a SIC code, publishes it for free in the submissions API, and lets
it be enumerated in reverse -- every filer under a code -- which is what cohort
membership actually needs.

SIC is coarser than Finviz, and this module is where that gap is paid for
explicitly rather than absorbed silently. Three layers, in order:

  1. ``SIC_COHORTS`` maps a code to a cohort. This settles 85 of the 91 current
     members on the SEC's classification alone.
  2. ``SIC_OVERRIDES`` names the thirteen filers whose EDGAR code is wrong,
     stale, or too coarse to separate an integrated major from a pure-play,
     one entry each, with the reason.
  3. ``EXCLUDED`` names filers that sit in a producing code and produce nothing.
     Each entry was established by ``scripts/check_producers.py`` reading the
     filing, not by reading the company's name.

The thing SIC cannot do is decide, on its own, that a candidate belongs. SIC
1311 sweeps in shells, midstream partnerships, refiners and -- observed in the
population -- a biotechnology company. That is why membership is gated on a
recorded producer verdict rather than on the code alone: the code proposes, the
filing disposes.
"""

from __future__ import annotations

from basin.edgar.discovery import FilerProfile

# The SIC codes whose filers own hydrocarbon reserves, and the cohort each maps
# to. The distinction is what the company owns, not what it handles: a driller
# and a service company sell to producers, midstream gathers and processes
# third-party volumes under fee contracts, and a refiner buys crude and sells
# products. None of them have a reserve base, a lifting cost or a production
# volume, which is to say none of them have the metrics Basin's schema is made
# of.
#
# 6792 "Oil Royalty Traders" is EDGAR's own code for royalty trusts and mineral
# vehicles. It maps into the E&P cohort -- those filers publish full reserve
# tables and are real comparables for each other -- but it also settles
# ``is_operator`` outright, which the previous name-substring guess could only
# approximate. This is the one place SIC is strictly better than Finviz, which
# gave royalty trusts and operators the same label.
#
# Integrated stays its own cohort rather than joining E&P: an integrated filer's
# production is one segment of a larger business, so its consolidated figures
# are not comparable to a pure-play E&P's without segment-level extraction that
# does not exist yet.
SIC_COHORTS: dict[str, str] = {
    "1311": "Oil & Gas E&P",         # Crude Petroleum & Natural Gas
    "6792": "Oil & Gas E&P",         # Oil Royalty Traders
    "2911": "Oil & Gas Integrated",  # Petroleum Refining
}

# The wider oil & gas population, for surveying rather than ingesting. Nothing
# here has a KPI schema, so nothing here can be a cohort; the codes are listed
# because knowing a filer is midstream is what keeps it out of a reserves table,
# and that is a decision worth being able to point at.
#
# Coal and uranium, which the Finviz cohort list carried, are absent: Basin has
# no reserve schema for either, so enumerating them would produce candidates
# nothing downstream could read.
NON_PRODUCING_SIC: dict[str, str] = {
    "1381": "Drilling Oil & Gas Wells",
    "1389": "Oil & Gas Field Services",
    "4922": "Natural Gas Transmission",
    "4923": "Natural Gas Transmission & Distribution",
    "4924": "Natural Gas Distribution",
    "5171": "Petroleum Bulk Stations & Terminals",
    "5172": "Petroleum & Petroleum Products Wholesalers",
}

# SIC codes that assign a non-operator outright: the filer holds an interest in
# production someone else lifts, so it reports no lifting cost and no capex.
NON_OPERATOR_SIC: frozenset[str] = frozenset({"6792"})

# Royalty, minerals and trust vehicles that file under 1311 rather than 6792 --
# EDGAR is inconsistent about which they get, and Black Stone Minerals and
# Dorchester Minerals are both 1311. The name hint still earns its keep for
# those; it is a fallback, not the primary signal.
NON_OPERATOR_HINTS = ("royalt", "minerals", "trust")

# Filers whose EDGAR SIC does not describe the business they are in. Each is a
# deviation from the SEC's own classification, so each carries its reason.
#
# The code is not re-derived per run: a SIC that changes is a real event about
# the filer, and it should surface as a disagreement with this table rather than
# move a company between cohorts silently.
SIC_OVERRIDES: dict[str, tuple[str, str]] = {
    # The integrated majors that EDGAR codes 1311, not 2911.
    #
    # This is the one place SIC is materially worse than the classification it
    # replaced, and it is worth being precise about why. A SIC code records what
    # a registrant registered as, and every one of these registered as an oil &
    # gas producer -- which they are, alongside refining, chemicals and
    # marketing. The US majors landed in 2911 and the non-US ones in 1311, for
    # no reason visible in the filings.
    #
    # Left uncorrected, Shell and TotalEnergies would join the E&P cohort, and
    # the panel would put an integrated filer's consolidated per-BOE costs next
    # to a pure-play's. That is precisely the comparability this schema exists
    # to refuse: a cohort IS a KPI schema.
    "0000879764": (
        "Oil & Gas Integrated",
        "TotalEnergies SE -- coded 1311. Refining & Chemicals and Integrated "
        "Power are reported segments alongside Exploration & Production",
    ),
    "0001002242": (
        "Oil & Gas Integrated",
        "Eni SpA -- coded 1311. Reports Refining, Chemicals (Versalis) and "
        "Plenitude alongside Exploration & Production",
    ),
    "0001119639": (
        "Oil & Gas Integrated",
        "Petrobras -- coded 1311. Refining, Transportation & Marketing is a "
        "reported segment; it refines the majority of what it lifts",
    ),
    "0001306965": (
        "Oil & Gas Integrated",
        "Shell plc -- coded 1311. Chemicals & Products, Marketing and "
        "Integrated Gas are reported segments alongside Upstream",
    ),
    "0001444406": (
        "Oil & Gas Integrated",
        "Ecopetrol S.A. -- coded 1311. Refining and midstream (Cenit, "
        "Reficar, Barrancabermeja) are reported segments alongside upstream",
    ),
    "0001475260": (
        "Oil & Gas Integrated",
        "Cenovus Energy -- coded 1311. Runs Canadian and US refining "
        "(Lima, Toledo, Superior) as a reported downstream segment",
    ),
    "0001922446": (
        "Oil & Gas Integrated",
        "Diversified Energy -- coded 1311. Carried forward from the previous "
        "classification, which placed it here for its owned midstream and well "
        "retirement business rather than for refining. The weakest entry in "
        "this table: it is closer to a pure-play Appalachian gas producer than "
        "to a major, and it is worth re-deciding on the segments its 10-K "
        "actually reports",
    ),

    "0001163165": (
        "Oil & Gas E&P",
        "ConocoPhillips -- coded 2911 Petroleum Refining, which it has not been "
        "since it spun off Phillips 66 in 2012. It is a pure-play E&P and its "
        "10-K carries no refining segment; the code is stale, not wrong at the "
        "time it was set",
    ),
    "0001279495": (
        "Oil & Gas E&P",
        "Baytex Energy -- coded 1381 Drilling Oil & Gas Wells. It is a Canadian "
        "operator holding its own reserves, not a contract driller",
    ),
    "0001792849": (
        "Oil & Gas E&P",
        "HighPeak Energy -- coded 1381 Drilling Oil & Gas Wells. A Midland "
        "Basin operator; its 10-K reports proved reserves and production",
    ),
    "0002093507": (
        "Oil & Gas E&P",
        "Greenland Energy -- coded 1381 Drilling Oil & Gas Wells. Recently "
        "listed and not yet in the corpus, so its producer verdict is 'unknown' "
        "and it is held out of membership until a filing has been read",
    ),
    "0000070145": (
        "Oil & Gas Integrated",
        "National Fuel Gas -- coded 4924 Natural Gas Distribution. It is a "
        "diversified filer whose Seneca Resources segment holds Appalachian "
        "reserves, so it reports the E&P metrics alongside utility ones. "
        "Integrated for the same reason the majors are: production is one "
        "segment of a larger business",
    ),
    "0001812447": (
        "Oil & Gas Integrated",
        "Sky Quarry -- coded 4955 Hazardous Waste Management. It recycles "
        "asphalt shingles into oil and also owns a refinery, so it spans "
        "extraction and processing",
    ),
}

# Filers that sit in a producing SIC code and hold no reserves. A non-producer
# in a producing cohort does not fail loudly: it renders as a blank row in a
# reserves panel, which reads as a filer that failed to tag something rather
# than one with nothing to report.
#
# Every entry below was established by reading the filing -- no reserve or
# production concepts tagged, and no reserve language in the annual report --
# and is recorded in ``producer_check``. They are named here as well as read
# from the store so that the reason travels with the decision in the source
# tree, and so a fresh database excludes them before the check has ever run.
#
# One Finviz-era exclusion is gone: MHM, a Bank of America structured note that
# the screener placed in an operating industry. It has no oil & gas SIC code,
# so enumerating from EDGAR never proposes it. That class of error does not
# arise here.
EXCLUDED: dict[str, str] = {
    "0000931427": "TGS, Transportadora de Gas del Sur -- Argentine gas pipeline; "
                  "EDGAR registers it as GAS TRANSPORTER OF THE SOUTH INC. 0 "
                  "reserve-language hits in 1.27M characters of its 20-F; tags "
                  "only revenue and capex",
    "0001043186": "SLNG, Stabilis Solutions -- small-scale LNG production and "
                  "distribution, not upstream. 0 reserve-language hits in its 10-K",
    "0001450704": "VIVK, Vivakor -- oilfield waste remediation and crude "
                  "transport. 0 reserve-language hits in its 10-K",
    "0000352955": "CKX, CKX Lands -- Louisiana land company that leases acreage "
                  "rather than operating it. Its 10-K states the position "
                  "outright: reserve information \"is not available. A schedule "
                  "indicating such reserve quantities is, therefore, not "
                  "presented.\" Tags only revenue and capex",
    "0000072633": "NRT, North European Oil Royalty Trust -- passive royalty on "
                  "German concessions. Its 4 reserve-language hits are all "
                  "risk-factor prose about the underlying assets depleting; it "
                  "discloses no reserve quantities of its own, unlike the US "
                  "royalty trusts in the cohort, which publish full reserve tables",
}


def producing_sic() -> tuple[str, ...]:
    """The SIC codes to enumerate for cohort membership."""
    return tuple(sorted(SIC_COHORTS))


def cohort_for(profile: FilerProfile) -> tuple[str | None, str]:
    """The cohort *profile* belongs to, and where that assignment came from.

    Returns ``(None, reason)`` when no cohort applies, so a caller reporting on
    a candidate can say why it was passed over rather than merely omitting it.
    """
    override = SIC_OVERRIDES.get(profile.cik)
    if override:
        return override[0], "sic-override"

    cohort = SIC_COHORTS.get(profile.sic)
    if cohort:
        return cohort, "sic"

    known = NON_PRODUCING_SIC.get(profile.sic)
    if known:
        return None, f"SIC {profile.sic} {known} -- holds no reserves"
    return None, f"SIC {profile.sic or '?'} {profile.sic_description or 'unclassified'}"


def is_operator(profile: FilerProfile) -> bool:
    """Whether the filer lifts its own hydrocarbons, or holds an interest in
    someone else's.

    Royalty and minerals vehicles stay in the E&P cohort -- they are real
    comparables for each other -- but the flag keeps them out of operator peer
    tables, where a blank lifting cost is a fact about the business model rather
    than a coverage gap.
    """
    if profile.sic in NON_OPERATOR_SIC:
        return False
    lowered = profile.name.lower()
    return not any(hint in lowered for hint in NON_OPERATOR_HINTS)
