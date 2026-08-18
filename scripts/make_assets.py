"""Generate the favicon and social share card.

Run after changing the brand marks:

    python scripts/make_assets.py

Outputs into ``src/basin/web/static``. The card pulls its figures from the
fact store when one is present, so the image a link preview shows is the size
of the actual dataset rather than a number typed into a design file.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

STATIC = Path("src/basin/web/static")
DB = Path("data/basin.db")

BLACK = (0, 0, 0)
GREEN = (0, 229, 133)
TEXT = (242, 242, 242)
MUTED = (122, 122, 122)
DIM = (74, 74, 74)
LINE = (26, 26, 26)

SANS = "/System/Library/Fonts/SFNS.ttf"
MONO = "/System/Library/Fonts/SFNSMono.ttf"


def font(path: str, size: int, weight: float | None = None) -> ImageFont.FreeTypeFont:
    f = ImageFont.truetype(path, size)
    if weight is not None:
        try:
            f.set_variation_by_axes([weight])
        except (OSError, AttributeError):
            pass  # static font: the base weight is what we get
    return f


def tracked(draw, xy, text, fnt, fill, tracking=0.0):
    """Draw text with letter-spacing, which PIL does not support natively."""
    x, y = xy
    for ch in text:
        draw.text((x, y), ch, font=fnt, fill=fill)
        x += draw.textlength(ch, font=fnt) + tracking
    return x


def tracked_width(draw, text, fnt, tracking=0.0) -> float:
    return sum(draw.textlength(c, font=fnt) for c in text) + tracking * max(len(text) - 1, 0)


def draw_mark(draw, cx, cy, size):
    """The Basin mark: stratigraphic layers narrowing with depth.

    Two earlier attempts failed at small sizes for the same reason -- curves
    need anti-aliasing to read, and a 16px favicon has no pixels to spare.
    A dot over a curve looked like a smiley; nested arcs looked like a wifi
    glyph. Rectangles have neither problem: they stay crisp at any size, and
    layers narrowing downward is what a sedimentary basin actually is.
    """
    widths = (1.0, 0.76, 0.52, 0.28)
    bar = size * 0.145
    gap = size * 0.088
    block = len(widths) * bar + (len(widths) - 1) * gap
    y = cy - block / 2
    radius = bar / 2
    for w in widths:
        half = size * w / 2
        draw.rounded_rectangle(
            [cx - half, y, cx + half, y + bar], radius=radius, fill=GREEN
        )
        y += bar + gap


def make_favicon() -> None:
    # Rendered large and downsampled: PIL's arc is jagged at small sizes.
    for out, px in [("favicon-32.png", 32), ("favicon-180.png", 180)]:
        scale = 8
        img = Image.new("RGBA", (px * scale, px * scale), BLACK + (255,))
        d = ImageDraw.Draw(img)
        draw_mark(d, px * scale / 2, px * scale / 2, px * scale * 0.62)
        img.resize((px, px), Image.LANCZOS).save(STATIC / out)

    # Vector version for browsers that prefer it, and for crisp scaling.
    (STATIC / "favicon.svg").write_text(
        '<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 64 64">\n'
        '  <rect width="64" height="64" fill="#000"/>\n'
        '  <g fill="#00e585">\n'
        '    <rect x="12" y="17" width="40" height="6" rx="3"/>\n'
        '    <rect x="17" y="27" width="30" height="6" rx="3"/>\n'
        '    <rect x="22" y="37" width="20" height="6" rx="3"/>\n'
        '    <rect x="27" y="47" width="10" height="6" rx="3"/>\n'
        '  </g>\n'
        "</svg>\n"
    )


def dataset_stats() -> list[tuple[str, str]]:
    if not DB.exists():
        return []
    conn = sqlite3.connect(f"file:{DB}?mode=ro", uri=True)
    try:
        one = lambda q: conn.execute(q).fetchone()[0]  # noqa: E731
        return [
            (f"{one('SELECT COUNT(*) FROM company'):,}", "COMPANIES"),
            (f"{one('SELECT COUNT(*) FROM fact'):,}", "FACTS"),
            (f"{one('SELECT COUNT(*) FROM filing'):,}", "FILINGS CITED"),
        ]
    finally:
        conn.close()


def make_share_card() -> None:
    W, H = 1200, 630
    img = Image.new("RGB", (W, H), BLACK)
    d = ImageDraw.Draw(img)

    d.rectangle([0, 0, W, 3], fill=GREEN)

    draw_mark(d, 92, 92, 46)

    wordmark = font(SANS, 34, 700)
    tracked(d, (132, 74), "BASIN", wordmark, TEXT, tracking=7)

    headline = font(SANS, 52, 600)
    for i, line in enumerate(
        ["Peer-comparable financial data", "for US oil & gas producers."]
    ):
        d.text((88, 190 + i * 66), line, font=headline, fill=TEXT)

    sub = font(SANS, 27, 400)
    d.text((88, 344), "Extracted from SEC filings, with a citation", font=sub, fill=MUTED)
    d.text((88, 380), "behind every number.", font=sub, fill=MUTED)

    stats = dataset_stats()
    if stats:
        d.rectangle([88, 470, W - 88, 471], fill=LINE)
        num_f, lbl_f = font(MONO, 40, 500), font(SANS, 15, 500)
        x = 88
        for value, label in stats:
            d.text((x, 508), value, font=num_f, fill=GREEN if label == "FACTS" else TEXT)
            tracked(d, (x + 3, 562), label, lbl_f, DIM, tracking=1.6)
            x += max(
                d.textlength(value, font=num_f),
                tracked_width(d, label, lbl_f, 1.6),
            ) + 96

    tag = font(MONO, 17, 400)
    text = "SEC EDGAR · XBRL"
    d.text((W - 88 - d.textlength(text, font=tag), 88), text, font=tag, fill=DIM)

    img.save(STATIC / "og.png")


if __name__ == "__main__":
    STATIC.mkdir(parents=True, exist_ok=True)
    make_favicon()
    make_share_card()
    for f in ["favicon-32.png", "favicon-180.png", "favicon.svg", "og.png"]:
        print(f"  {STATIC / f}  {(STATIC / f).stat().st_size:,} bytes")
