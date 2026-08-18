"""Turn a filing's HTML into searchable text.

Filings are HTML built for print, not for parsing: numbers sit in deeply
nested table cells, separated by non-breaking spaces, sometimes split across
inline tags. The goal here is not to reconstruct the document's structure but
to make its *numbers* findable and quotable, so the text keeps reading order
and collapses everything else.
"""

from __future__ import annotations

import html as html_module
import re

_DROP = re.compile(r"(?is)<(script|style|head)[^>]*>.*?</\1>")
_TAG = re.compile(r"<[^>]+>")
# Cell and row boundaries have to survive as whitespace, or adjacent table
# cells fuse into a single meaningless number.
_BLOCK = re.compile(r"(?i)</(td|th|tr|p|div|table|li|h[1-6])>")
_SPACES = re.compile(r"[\s   ]+")


def html_to_text(raw: str) -> str:
    """Flatten filing HTML to a single normalised line of text."""
    cleaned = _DROP.sub(" ", raw)
    cleaned = _BLOCK.sub(" \n ", cleaned)
    cleaned = _TAG.sub(" ", cleaned)
    cleaned = html_module.unescape(cleaned)
    # Unicode minus and en-dash appear in negative figures.
    cleaned = cleaned.replace("−", "-").replace("–", "-")
    return _SPACES.sub(" ", cleaned).strip()


def snippet(text: str, start: int, end: int, *, pad: int = 110) -> str:
    """Quote the neighbourhood of a match, for use as a source span."""
    lo = max(0, start - pad)
    hi = min(len(text), end + pad)
    return ("… " if lo else "") + text[lo:hi].strip() + (" …" if hi < len(text) else "")
