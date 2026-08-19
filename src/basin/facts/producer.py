"""Does this filer actually produce oil, gas or NGL?

Cohort membership comes from Finviz, and Finviz's classification is good but not
clean. Two errors have already surfaced: MHM, a Bank of America structured note
filed under Oil & Gas Equipment & Services, and TGS -- Transportadora de Gas del
Sur, an Argentine pipeline operator that EDGAR registers as "GAS TRANSPORTER OF
THE SOUTH INC." -- classified as Oil & Gas Integrated.

A misclassified filer is worse than an absent one. It sits in a reserves panel
as a blank row, and a blank row reads as a coverage gap -- a company that failed
to tag something -- rather than as a company with nothing to report. The panel
then understates its own completeness and invites someone to go looking for data
that does not exist.

The test is the one a non-producer fails by definition: a company that lifts
hydrocarbons has a reserve base, and says so somewhere. Two independent places
to look, either of which is sufficient:

  * **Tagged reserve or production concepts.** Cheap and exact, but silent for
    IFRS filers, since ifrs-full has no reserve concept for anyone to tag.
  * **Reserve language in the annual report.** Reaches the IFRS filers, and is
    the only evidence available for a filer whose XBRL is thin.

Evidence of neither is not proof of absence. A filer with no facts and no
document in the corpus returns ``unknown`` -- there was nothing to test -- and
that is deliberately distinct from ``non-producer``, which means the filing was
read and holds no reserves.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

# Concepts that only a company with a reserve base can report. Revenue and capex
# are deliberately absent: a pipeline has both, and TGS's entire fact footprint
# is those two concepts, which is exactly the signature being caught.
RESERVE_CONCEPTS = (
    "proved_developed_reserves_boe",
    "proved_undeveloped_reserves_boe",
    "proved_reserves_boe",
    "standardized_measure",
    "production_volume",
    "average_sales_price",
    "production_cost_per_boe",
)

# Phrases a reserve disclosure cannot avoid, under either regime. "proved plus
# probable" and "forecast prices" are the NI 51-101 forms; the rest are Subpart
# 1200. Matched case-insensitively against the flattened document.
RESERVE_PHRASES = (
    "proved reserves",
    "proved developed",
    "proved undeveloped",
    "proved plus probable",
    "standardized measure",
    "reserve life",
    "forecast prices",
)

# One stray mention is not a disclosure -- an equipment supplier can mention its
# customers' proved reserves in a risk factor. A real reserve section repeats
# these phrases across tables and notes; the filers checked so far land between
# 18 and 195 hits, and the non-producer landed on zero.
MIN_PHRASE_HITS = 5

PRODUCER = "producer"
NON_PRODUCER = "non-producer"
UNKNOWN = "unknown"

_TAG_RE = re.compile(r"<[^>]+>")
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class ProducerCheck:
    """One verdict, and the evidence that produced it."""

    cik: str
    ticker: str | None
    name: str
    verdict: str
    concepts: tuple[str, ...] = ()
    """Reserve or production concepts this filer tags."""

    phrase_hits: int = 0
    """Reserve-language hits in the annual report that was read."""

    document: str | None = None
    """Which document was read, so the verdict can be re-checked."""

    documents_read: int = 0
    note: str = ""

    @property
    def is_producer(self) -> bool:
        return self.verdict == PRODUCER


def flatten(html: str) -> str:
    """Strip markup to searchable text. Same treatment for every document."""
    return _WS_RE.sub(" ", _TAG_RE.sub(" ", html))


def count_reserve_phrases(text: str) -> int:
    """How many times *text* uses language only a reserve disclosure uses."""
    lowered = text.lower()
    return sum(lowered.count(phrase) for phrase in RESERVE_PHRASES)


def judge(
    *,
    cik: str,
    ticker: str | None,
    name: str,
    concepts: tuple[str, ...] | list[str],
    phrase_hits: int,
    documents_read: int,
    document: str | None = None,
    min_hits: int = MIN_PHRASE_HITS,
) -> ProducerCheck:
    """Decide from evidence already gathered. Pure -- no I/O, so it is testable.

    Either signal alone is enough to confirm a producer. Only the absence of
    both, *with something actually having been read*, is a negative verdict.
    """
    concepts = tuple(concepts)
    common = dict(cik=cik, ticker=ticker, name=name, concepts=concepts,
                  phrase_hits=phrase_hits, documents_read=documents_read,
                  document=document)

    if concepts:
        return ProducerCheck(
            verdict=PRODUCER, **common,
            note=f"tags {len(concepts)} reserve/production concept(s)",
        )
    if phrase_hits >= min_hits:
        return ProducerCheck(
            verdict=PRODUCER, **common,
            note=f"no reserve concepts tagged, but the filing discusses "
                 f"reserves ({phrase_hits} hits)",
        )
    if documents_read == 0:
        return ProducerCheck(
            verdict=UNKNOWN, **common,
            note="no reserve concepts and no filing in the corpus -- nothing "
                 "was tested, which is not the same as nothing being there",
        )
    return ProducerCheck(
        verdict=NON_PRODUCER, **common,
        note=f"read {documents_read} document(s) of the annual report; no "
             f"reserve concepts tagged and {phrase_hits} reserve-language "
             f"hit(s), below the {min_hits} required",
    )
