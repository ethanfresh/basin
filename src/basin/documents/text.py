"""Turn filing HTML into text that a number can be *located* in.

Flattening a filing to one long string is enough to check that a value is
present. It is not enough to tell a reader where to look: a citation that says
"somewhere in this 3MB document" is not much better than no citation. So the
parse keeps the two coordinates a person actually uses — the page and the line
— alongside the flat text the search runs over.

Pages are real. EDGAR HTML separates them with ``<hr>`` carrying
``page-break-after``: Gulfport's 10-K has 107 of them, matching its printed
pages. Lines come from block boundaries, because a table row rendered as one
line is the unit a reader scans.
"""

from __future__ import annotations

import html as html_module
import re
from bisect import bisect_right
from dataclasses import dataclass, field

# ix:header is the filing's hidden XBRL preamble: contexts, units and
# dei facts, rendered display:none. It is data, not page content -- leaving it
# in put the entire block on "sheet 1", made its text findable, and pushed a
# reader's page numbers off by however many sheets it spanned.
_DROP = re.compile(
    r"(?is)<(script|style|head)[^>]*>.*?</\1>|<ix:header[^>]*>.*?</ix:header>"
)
_PAGE_BREAK = re.compile(r"(?i)<hr\b[^>]*>")
_BLOCK = re.compile(r"(?i)</(td|th|tr|p|div|table|li|h[1-6])\s*>")
_TAG = re.compile(r"<[^>]+>")
_INLINE_WS = re.compile(r"[ \t\r\f\v   ]+")


@dataclass(frozen=True)
class Line:
    """One readable line, with the coordinates a reader can act on."""

    page: int
    line: int
    start: int
    end: int
    text: str
    raw_start: int = 0
    """Offset of this line in the original HTML.

    Kept so a hit in the markup — an ``<ix:nonFraction>`` at some position in
    the raw document — can be turned into the page and line a reader sees.
    Without it the two coordinate systems cannot be joined.
    """
    raw_end: int = 0


@dataclass
class Document:
    """A filing, flattened for search but still locatable."""

    text: str
    lines: list[Line] = field(default_factory=list)
    folios: dict[int, int] = field(default_factory=dict, repr=False)
    """Derived page number -> the number actually printed on that page."""

    _starts: list[int] = field(default_factory=list, repr=False)

    @property
    def pages(self) -> int:
        return self.lines[-1].page if self.lines else 0

    def folio(self, page: int) -> int | None:
        """The number printed on *page*, when the filing prints one."""
        return self.folios.get(page)

    def locate(self, offset: int) -> Line | None:
        """The line containing *offset* in the flattened text."""
        if not self.lines:
            return None
        i = bisect_right(self._starts, offset) - 1
        return self.lines[max(0, i)]

    def locate_raw(self, raw_offset: int) -> Line | None:
        """The line containing *raw_offset* in the original HTML."""
        if not self.lines:
            return None
        starts = [line.raw_start for line in self.lines]
        i = bisect_right(starts, raw_offset) - 1
        return self.lines[max(0, i)]


def _clean(fragment: str) -> str:
    fragment = _TAG.sub(" ", fragment)
    fragment = html_module.unescape(fragment)
    fragment = fragment.replace("−", "-").replace("–", "-").replace("​", "")
    return _INLINE_WS.sub(" ", fragment).strip()


def parse(raw: str) -> Document:
    """Parse filing HTML into flat text plus page/line coordinates.

    Positions in the original HTML are carried through, because a figure found
    in the markup is located by raw offset while a reader is told a page and a
    line. Joining those needs both, so the walk is done with `finditer` rather
    than `split`.
    """
    # Dropped regions are replaced by an equal number of spaces rather than a
    # single one, so every offset into `body` is also an offset into `raw`.
    # Otherwise a position found in the markup and a position found in the text
    # are in different coordinate systems -- currently only 96 characters
    # apart, but a filing with a large embedded stylesheet would shift a
    # citation onto the wrong page.
    body = _DROP.sub(lambda m: " " * len(m.group(0)), raw)

    lines: list[Line] = []
    starts: list[int] = []
    chunks: list[str] = []
    cursor = 0

    # Page spans, as (raw_start, raw_end).
    breaks = [m.span() for m in _PAGE_BREAK.finditer(body)]
    bounds: list[tuple[int, int]] = []
    previous = 0
    for start, end in breaks:
        bounds.append((previous, start))
        previous = end
    bounds.append((previous, len(body)))

    for page_number, (page_start, page_end) in enumerate(bounds, start=1):
        segment_start = page_start
        # Block boundaries become line boundaries; without them adjacent table
        # cells fuse and produce numbers the filing never contained.
        boundaries = [m.end() for m in _BLOCK.finditer(body, page_start, page_end)]
        boundaries.append(page_end)
        for boundary in boundaries:
            text = _clean(body[segment_start:boundary])
            if text:
                start = cursor
                end = start + len(text)
                lines.append(
                    Line(page_number, len(lines) + 1, start, end, text,
                         segment_start, boundary)
                )
                starts.append(start)
                chunks.append(text)
                cursor = end + 1  # the joining newline
            segment_start = boundary

    folios: dict[int, int] = {}
    # D6. The number printed at the foot of a page need not equal the count of
    # page breaks: cover pages and contents are often unnumbered. Both are kept
    # so a citation can quote the printed folio and the mismatch is measurable
    # rather than assumed away.
    by_page: dict[int, list[Line]] = {}
    for line in lines:
        by_page.setdefault(line.page, []).append(line)
    for page_number, page_lines in by_page.items():
        for candidate in reversed(page_lines[-3:]):
            folio = _FOLIO_RE.search("\n" + candidate.text)
            if folio:
                folios[page_number] = int(folio.group(1))
                break

    return Document(
        text="\n".join(chunks), lines=lines, _starts=starts, folios=folios
    )


def html_to_text(raw: str) -> str:
    """Flat searchable text. Kept for callers that do not need coordinates."""
    return parse(raw).text


def snippet(text: str, start: int, end: int, *, pad: int = 110) -> str:
    """Quote the neighbourhood of a match, for use as a source span."""
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    return ("… " if lo else "") + text[lo:hi].strip() + (" …" if hi < len(text) else "")


# --- coordinates over the flat text ---------------------------------------
#
# `parse` gives page and line together, which is what a reader wants. These
# two work on the flat string alone, for callers that already hold text and
# only need to say where in it something sits.

# D5. Filings put the item number and its title in separate table cells, so
# they arrive as separate lines: "Item\n1.\nBusiness". Matching across the
# break, then normalising the whitespace, is what makes the captured heading
# usable -- and a canonical "Item 1" is stored apart from the display title so
# the two are not conflated.
#
# The gap between number and title is any amount of horizontal space -- EQT
# sets its headings with eight non-breaking spaces -- but at most one line
# break, so the match cannot reach across a paragraph to borrow a title.
_GAP = r"[^\S\n]*\n?[^\S\n]*"
_SECTION_RE = re.compile(
    r"(?im)^[^\S\n]*(Item)" + _GAP + r"(\d{1,2}[A-Z]?)[^\S\n]*\.?" + _GAP
    + r"([A-Z][^\n]{0,90})$"
)

# The printed folio: filings emit the page number immediately before the page
# break, usually followed by the next page's running header.
_FOLIO_RE = re.compile(r"(?s)(?:^|\n)\s*(?:Page\s+)?(\d{1,4})\s*$")

# D9. The contents page lists every item heading before the body starts, so
# "the last heading before this offset" answers Item 16 for anything early in
# a 10-K -- a figure on page 11 was being filed under "Form 10-K Summary". A
# contents row carries the page it points to, either at the end of its own
# line ("Business .... 8") or on the next line, which is how the filing itself
# distinguishes the listing from the section. One such row is not proof: an
# empty "Item 6. [Reserved]" can sit just above a printed folio. A run of them
# is, so rows are only discarded in company.
#
# A contents block restarts its numbering at each Part, so a restart does not
# end it -- Chesapeake's 10-Q lists 1, 2, 3, 4 and then 1, 1A, 2 ... 6. What
# does end it is a restart too short to be another Part: Comstock's real
# Item 1 sits directly under the block and above its page's folio, which is a
# contents row in every respect except that it is the section itself.
_LISTING_TAIL = re.compile(r"[ \t.·\u2026-]*\d{1,4}\s*$")
_LISTING_NEXT = re.compile(r"\n[ \t]*(?:Page[ \t]+)?\d{1,4}[ \t]*(?:\n|$)")
_CONTENTS_RUN = 4
_ITEM_NUMBER = re.compile(r"(?i)(\d+)([A-Z]?)")


def line_of(text: str, offset: int) -> int:
    """1-based line number containing *offset*."""
    return text.count("\n", 0, max(0, offset)) + 1


def _heading(match: re.Match) -> tuple[str, str]:
    """``(canonical, display)`` for a matched Item heading."""
    number = match.group(2).strip()
    title = _INLINE_WS.sub(" ", match.group(3)).strip(" .")
    return f"Item {number}", f"Item {number}. {title}"


def _is_listing(text: str, match: re.Match) -> bool:
    """Does this heading point at a page, rather than start one?"""
    if _LISTING_TAIL.search(match.group(3)):
        return True
    return bool(_LISTING_NEXT.match(text, match.end()))


def _item_order(match: re.Match) -> tuple[int, str]:
    """Where an item number falls in the list: 1 < 1A < 1B < 2."""
    number, suffix = _ITEM_NUMBER.match(match.group(2).strip()).groups()
    return int(number), suffix.upper()


def _survivors(block: list[re.Match]) -> list[re.Match]:
    """The headings a block of contents rows should not have swallowed.

    A block is only a table of contents once `_CONTENTS_RUN` rows have run
    together; anything shorter is left alone. Within one, the last restart of
    the numbering is either the next Part of the same contents or the first
    real heading of the body, and the two are told apart by what follows: a
    Part continues for several more rows, a body heading does not.
    """
    if len(block) < _CONTENTS_RUN:
        return block
    restart = 0
    for i in range(1, len(block)):
        if _item_order(block[i]) <= _item_order(block[i - 1]):
            restart = i
    tail = block[restart:]
    return tail if restart and len(tail) < _CONTENTS_RUN else []


def _drop_contents(text: str, matches: list[re.Match]) -> list[re.Match]:
    """Discard the table of contents from a document's headings."""
    kept: list[re.Match] = []
    block: list[re.Match] = []
    for match in matches:
        if _is_listing(text, match):
            block.append(match)
            continue
        kept.extend(_survivors(block))
        block.clear()
        kept.append(match)
    kept.extend(_survivors(block))
    return kept


def section_index(text: str) -> list[tuple[int, str]]:
    """Every "Item N." heading and where it starts, in one pass.

    Looking a heading up per line would rescan the document each time, which is
    fine once and quadratic when labelling every line of a filing. Building the
    index once and searching it turns indexing a 2,000-line document from
    minutes into milliseconds.
    """
    matches = _drop_contents(text, list(_SECTION_RE.finditer(text)))
    return [(m.start(), _heading(m)[1]) for m in matches]


def section_of(text: str, offset: int) -> str | None:
    """The nearest preceding "Item N." heading, which is how filings are cited.

    Returns None rather than a guess when the match sits before any heading —
    exhibits and cover pages genuinely have none. The whole document is scanned
    even for an early offset, because the contents block that has to be
    discarded is only recognisable as a run.
    """
    return section_at(section_index(text), offset)


def section_at(index: list[tuple[int, str]], offset: int) -> str | None:
    """The heading in force at *offset*, given a prebuilt `section_index`."""
    if not index:
        return None
    lo, hi = 0, len(index)
    while lo < hi:
        mid = (lo + hi) // 2
        if index[mid][0] <= offset:
            lo = mid + 1
        else:
            hi = mid
    return index[lo - 1][1] if lo else None
