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
_SECTION_RE = re.compile(
    r"(?im)^[ \t]*(Item)[\s\n]{0,4}(\d{1,2}[A-Z]?)\s*\.?[\s\n]{0,4}"
    r"([A-Z][^\n]{0,90})$"
)

# The printed folio: filings emit the page number immediately before the page
# break, usually followed by the next page's running header.
_FOLIO_RE = re.compile(r"(?s)(?:^|\n)\s*(?:Page\s+)?(\d{1,4})\s*$")


def line_of(text: str, offset: int) -> int:
    """1-based line number containing *offset*."""
    return text.count("\n", 0, max(0, offset)) + 1


def _heading(match: re.Match) -> tuple[str, str]:
    """``(canonical, display)`` for a matched Item heading."""
    number = match.group(2).strip()
    title = _INLINE_WS.sub(" ", match.group(3)).strip(" .")
    return f"Item {number}", f"Item {number}. {title}"


def section_of(text: str, offset: int) -> str | None:
    """The nearest preceding "Item N." heading, which is how filings are cited.

    Returns None rather than a guess when the match sits before any heading —
    exhibits and cover pages genuinely have none.
    """
    last = None
    for match in _SECTION_RE.finditer(text, 0, max(0, offset)):
        last = _heading(match)[1]
    return last


def section_index(text: str) -> list[tuple[int, str]]:
    """Every "Item N." heading and where it starts, in one pass.

    `section_of` rescans from the beginning for each call, which is fine for a
    single lookup and quadratic when labelling every line of a filing. Building
    the index once and searching it turns indexing a 2,000-line document from
    minutes into milliseconds.
    """
    return [(m.start(), _heading(m)[1]) for m in _SECTION_RE.finditer(text)]


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
