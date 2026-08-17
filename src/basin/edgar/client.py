"""HTTP client for the SEC's public EDGAR APIs.

Two SEC requirements are enforced here rather than at call sites, so there is
exactly one place they can be got wrong:

  * every request declares a ``User-Agent`` identifying the requester
  * requests are capped at 10 per second

No API key is involved. All endpoints are public.
"""

from __future__ import annotations

import os
import threading
import time
from typing import Any

import httpx

SEC_DATA_HOST = "https://data.sec.gov"
SEC_WWW_HOST = "https://www.sec.gov"

# SEC's published ceiling. We sit under it deliberately.
MAX_REQUESTS_PER_SECOND = 8.0

DEFAULT_TIMEOUT = 30.0
MAX_RETRIES = 4


class SECError(RuntimeError):
    """A request to an SEC endpoint failed in a way retrying did not fix."""


class NotFound(SECError):
    """The endpoint returned 404 — e.g. a CIK with no XBRL facts at all."""


def _user_agent() -> str:
    """The declared identity for SEC requests.

    SEC guidelines ask for a contact address. Configured by environment so the
    identity travels with the deployment rather than the source tree.
    """
    ua = os.environ.get("BASIN_SEC_USER_AGENT", "").strip()
    if not ua:
        raise SECError(
            "BASIN_SEC_USER_AGENT is not set. The SEC requires a User-Agent "
            'identifying the requester, e.g. "Basin research (you@example.com)". '
            "Set it in your environment or .env before making requests."
        )
    return ua


class _RateLimiter:
    """Thread-safe minimum-interval gate.

    Deliberately not a token bucket: bursting to a bucket's capacity is exactly
    the behaviour that gets an IP blocked, and nothing here needs burst.
    """

    def __init__(self, requests_per_second: float) -> None:
        self._min_interval = 1.0 / requests_per_second
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    def acquire(self) -> None:
        with self._lock:
            now = time.monotonic()
            wait = self._next_allowed - now
            if wait > 0:
                time.sleep(wait)
                now = time.monotonic()
            self._next_allowed = now + self._min_interval


class EdgarClient:
    """Rate-limited, retrying client for ``data.sec.gov`` and ``www.sec.gov``.

    Usable as a context manager; safe to share across threads.
    """

    def __init__(
        self,
        *,
        user_agent: str | None = None,
        timeout: float = DEFAULT_TIMEOUT,
        requests_per_second: float = MAX_REQUESTS_PER_SECOND,
    ) -> None:
        self._limiter = _RateLimiter(requests_per_second)
        self._client = httpx.Client(
            timeout=timeout,
            headers={
                "User-Agent": user_agent or _user_agent(),
                "Accept-Encoding": "gzip, deflate",
            },
            follow_redirects=True,
        )

    def __enter__(self) -> "EdgarClient":
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    def close(self) -> None:
        self._client.close()

    def get_json(self, url: str) -> dict[str, Any]:
        """GET *url* and parse JSON, honouring the rate cap and retrying.

        Raises :class:`NotFound` on 404 — for SEC endpoints that means the
        resource genuinely does not exist (no facts for this CIK), which is a
        result callers handle, not a transport failure.
        """
        last_error: Exception | None = None

        for attempt in range(MAX_RETRIES):
            self._limiter.acquire()
            try:
                response = self._client.get(url)
            except httpx.RequestError as exc:  # connection, DNS, timeout
                last_error = exc
                time.sleep(_backoff(attempt))
                continue

            if response.status_code == 404:
                raise NotFound(f"404 from SEC: {url}")

            # 403 shows up when the User-Agent is missing or the rate cap was
            # tripped. Retrying a malformed identity is pointless, so surface it.
            if response.status_code == 403:
                raise SECError(
                    f"403 from SEC: {url}. Check BASIN_SEC_USER_AGENT and request rate."
                )

            if response.status_code == 429 or response.status_code >= 500:
                last_error = SECError(f"{response.status_code} from SEC: {url}")
                time.sleep(_backoff(attempt))
                continue

            response.raise_for_status()
            return response.json()

        raise SECError(f"giving up on {url} after {MAX_RETRIES} attempts") from last_error


def _backoff(attempt: int) -> float:
    """Exponential backoff: 0.5s, 1s, 2s, 4s."""
    return 0.5 * (2**attempt)


def cik_padded(cik: int | str) -> str:
    """Format a CIK as the zero-padded 10-digit form the JSON APIs require."""
    digits = str(cik).strip().lstrip("CIK").lstrip("cik").strip()
    if not digits.isdigit():
        raise ValueError(f"not a CIK: {cik!r}")
    return digits.zfill(10)
