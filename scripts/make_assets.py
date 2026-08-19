"""Generate the favicon and social share card.

Run after changing the brand marks:

    python scripts/make_assets.py

Outputs into ``src/basin/web/static``.
"""

from __future__ import annotations

from pathlib import Path

from PIL import Image, ImageDraw

STATIC = Path("src/basin/web/static")

BLACK = (0, 0, 0)
GREEN = (0, 229, 133)


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


def make_share_card() -> None:
    """The mark alone on black -- a link preview is seen at thumbnail size,
    where anything more than the logo is unreadable anyway."""
    W, H = 1200, 630
    scale = 4
    img = Image.new("RGB", (W * scale, H * scale), BLACK)
    d = ImageDraw.Draw(img)
    draw_mark(d, W * scale / 2, H * scale / 2, 300 * scale)
    img.resize((W, H), Image.LANCZOS).save(STATIC / "og.png")


if __name__ == "__main__":
    STATIC.mkdir(parents=True, exist_ok=True)
    make_favicon()
    make_share_card()
    for f in ["favicon-32.png", "favicon-180.png", "favicon.svg", "og.png"]:
        print(f"  {STATIC / f}  {(STATIC / f).stat().st_size:,} bytes")
