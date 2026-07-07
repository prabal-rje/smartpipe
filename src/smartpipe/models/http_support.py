"""Shared HTTP plumbing for provider adapters.

httpx stays a function-local import: this module sits on the ``container`` path,
and ``--help`` must never pay for the HTTP stack (tests/test_startup_imports.py
is the gate).
"""

from __future__ import annotations

import time
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

    import httpx

__all__ = ["default_timeout", "is_retryable_http", "make_client", "retry_after_seconds"]


def default_timeout() -> httpx.Timeout:
    """Generous read timeout — local models can take a while per item (plan/architecture.md)."""
    import httpx

    return httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)


def make_client() -> httpx.AsyncClient:
    import httpx

    return httpx.AsyncClient(timeout=default_timeout())


def retry_after_seconds(exc: Exception, *, now: Callable[[], float] = time.time) -> float | None:
    """Seconds the server asked us to wait, from a ``Retry-After`` header.

    Accepts both wire forms (integer seconds and HTTP-date); anything absent,
    unparseable, or in the past is ``None`` — the caller falls back to its own
    backoff. Clamping hostile values is the retry loop's job, not the parser's.
    """
    import httpx

    if not isinstance(exc, httpx.HTTPStatusError):
        return None
    header = exc.response.headers.get("retry-after")
    if header is None:
        return None
    text = header.strip()
    if text.isdigit():  # RFC 7231 delay-seconds is non-negative; "-5" falls through
        return float(text)
    from email.utils import parsedate_to_datetime

    try:
        target = parsedate_to_datetime(text)
    except (TypeError, ValueError):
        return None
    if target.tzinfo is None:  # RFC 7231 dates are GMT; a bare date reads as UTC
        from datetime import UTC

        target = target.replace(tzinfo=UTC)
    delta = target.timestamp() - now()
    return delta if delta >= 0 else None


def is_retryable_http(exc: Exception) -> bool:
    import httpx

    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    # TimeoutException is the shared base of ConnectTimeout/ReadTimeout/
    # WriteTimeout/PoolTimeout — keying off it catches all four (the connect and
    # write variants are NOT subclasses of ConnectError/WriteError in httpx).
    return isinstance(
        exc,
        httpx.TimeoutException | httpx.ConnectError | httpx.RemoteProtocolError | httpx.WriteError,
    )
