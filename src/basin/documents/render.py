"""Render one sheet of a stored filing to an image.

The parser reads structure out of HTML; a person reads the rendered page. A
vision pass needs what the person sees, so sheets are rendered with headless
Chrome — the same engine the debug viewer uses — rather than reconstructed
from the parse being checked. Rendering the parser's own output would test
nothing.
"""

from __future__ import annotations

import shutil
import subprocess
import tempfile
import time
from pathlib import Path

from basin.documents import corpus
from basin.documents.text import _PAGE_BREAK

CHROME = "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome"

_STYLE = (
    "<style>body{background:#fff;color:#000;padding:24px;max-width:1100px;"
    "margin:auto;font-family:serif}</style>"
)


def sheet_html(accession: str, name: str, sheet: int) -> str | None:
    """The raw HTML of one sheet, split on the filing's own page breaks."""
    raw = corpus.load_raw(accession, name)
    if raw is None:
        return None
    bounds: list[tuple[int, int]] = []
    previous = 0
    for match in _PAGE_BREAK.finditer(raw):
        bounds.append((previous, match.start()))
        previous = match.end()
    bounds.append((previous, len(raw)))
    if not 1 <= sheet <= len(bounds):
        return None
    start, end = bounds[sheet - 1]
    return (
        "<html><head><meta charset='utf-8'>" + _STYLE + "</head><body>"
        + raw[start:end]
        + "</body></html>"
    )


def render_png(
    html: str,
    out_path: Path,
    *,
    width: int = 1100,
    height: int = 2400,
    timeout: int = 60,
) -> Path:
    """Screenshot *html* with headless Chrome.

    Height is generous because a printed page of a filing renders taller than
    a screen; a clipped table would make the vision check blame the parser for
    the renderer's crop.
    """
    with tempfile.NamedTemporaryFile(
        mode="w", suffix=".html", delete=False, encoding="utf-8"
    ) as handle:
        handle.write(html)
        source = Path(handle.name)
    # A dedicated user-data-dir keeps headless Chrome from contending with a
    # running desktop Chrome for the profile lock. And Chrome is not waited on
    # to exit: on this platform headless Chrome writes the screenshot and then
    # lingers (background service registrations keep it alive), so completion
    # is detected by the output file appearing, after which the process is
    # killed. Waiting on exit was a guaranteed timeout.
    profile = Path(tempfile.mkdtemp(prefix="basin-chrome-"))
    out_path.unlink(missing_ok=True)
    process = subprocess.Popen(
        [
            CHROME,
            "--headless=new",
            "--disable-gpu",
            "--no-first-run",
            "--no-default-browser-check",
            "--disable-extensions",
            f"--user-data-dir={profile}",
            "--hide-scrollbars",
            f"--window-size={width},{height}",
            f"--screenshot={out_path}",
            "--virtual-time-budget=4000",
            source.as_uri(),
        ],
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if out_path.exists() and out_path.stat().st_size > 0:
                # One extra beat so the write finishes before the kill.
                time.sleep(0.3)
                break
            if process.poll() is not None:
                break
            time.sleep(0.2)
        if not out_path.exists():
            raise RuntimeError(f"Chrome produced no screenshot for {out_path}")
    finally:
        process.kill()
        process.wait()
        source.unlink(missing_ok=True)
        shutil.rmtree(profile, ignore_errors=True)
    return out_path
