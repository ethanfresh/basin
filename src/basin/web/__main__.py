"""Run the dashboard: ``python -m basin.web``.

The port comes from the ``PORT`` environment variable so a supervisor can
assign one, falling back to 8422 for a plain local run. Nothing here needs a
fixed port — there are no OAuth callbacks, webhooks, or cross-origin callers.
"""

from __future__ import annotations

import os

import uvicorn


def main() -> None:
    uvicorn.run(
        "basin.web.app:app",
        host=os.environ.get("HOST", "127.0.0.1"),
        port=int(os.environ.get("PORT", "8422")),
    )


if __name__ == "__main__":
    main()
