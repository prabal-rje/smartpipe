"""Shared HTTP plumbing for provider adapters.

httpx stays a function-local import: this module sits on the ``container`` path,
and ``--help`` must never pay for the HTTP stack (tests/test_startup_imports.py
is the gate).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import httpx

__all__ = ["default_timeout", "is_retryable_http", "make_client"]


def default_timeout() -> httpx.Timeout:
    """Generous read timeout — local models can take a while per item (plan/architecture.md)."""
    import httpx

    return httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)


def make_client() -> httpx.AsyncClient:
    import httpx

    return httpx.AsyncClient(timeout=default_timeout())


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
