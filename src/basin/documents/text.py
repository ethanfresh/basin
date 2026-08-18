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

_DROP = re.compile(r"(?is)<(script|style|head)[^>]*>.*?</\1>")
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


@dataclass
class Document:
    """A filing, flattened for search but still locatable."""

    text: str
    lines: list[Line] = field(default_factory=list)
    _starts: list[int] = field(default_factory=list, repr=False)

    @property
    def pages(self) -> int:
        return self.lines[-1].page if self.lines else 0

    def locate(self, offset: int) -> Line | None:
        """The line containing *offset*."""
        if not self.lines:
            return None
        i = bisect_right(self._starts, offset) - 1
        return self.lines[max(0, i)]


def _clean(fragment: str) -> str:
    fragment = _TAG.sub(" ", fragment)
    fragment = html_module.unescape(fragment)
    fragment = fragment.replace("−", "-").replace("–", "-").replace("​", "")
    return _INLINE_WS.sub(" ", fragment).strip()


def parse(raw: str) -> Document:
    """Parse filing HTML into flat text plus page/line coordinates."""
    body = _DROP.sub(" ", raw)

    lines: list[Line] = []
    starts: list[int] = []
    chunks: list[str] = []
    cursor = 0

    for page_number, page_html in enumerate(_PAGE_BREAK.split(body), start=1):
        # Block boundaries become line boundaries; without them adjacent table
        # cells fuse and produce numbers the filing never contained.
        for raw_line in _BLOCK.sub("\n", page_html).split("\n"):
            text = _clean(raw_line)
            if not text:
                continue
            start = cursor
            end = start + len(text)
            lines.append(Line(page_number, len(lines) + 1, start, end, text))
            starts.append(start)
            chunks.append(text)
            cursor = end + 1  # the joining newline

    return Document(text="\n".join(chunks), lines=lines, _starts=starts)


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

_SECTION_RE = re.compile(
    r"(?im)^\s*(Item\s+\d{1,2}[A-Z]?\.?\s+[A-Z][^\n]{0,90})$"
)


def line_of(text: str, offset: int) -> int:
    """1-based line number containing *offset*."""
    return text.count("\n", 0, max(0, offset)) + 1


def section_of(text: str, offset: int) -> str | None:
    """The nearest preceding "Item N." heading, which is how filings are cited.

    Returns None rather than a guess when the match sits before any heading —
    exhibits and cover pages genuinely have none.
    """
    last = None
    for match in _SECTION_RE.finditer(text, 0, max(0, offset)):
        last = match.group(1).strip()
    return last


def section_index(text: str) -> list[tuple[int, str]]:
    """Every "Item N." heading and where it starts, in one pass.

    `section_of` rescans from the beginning for each call, which is fine for a
    single lookup and quadratic when labelling every line of a filing. Building
    the index once and searching it turns indexing a 2,000-line document from
    minutes into milliseconds.
    """
    return [(m.start(), m.group(1).strip()) for m in _SECTION_RE.finditer(text)]


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
