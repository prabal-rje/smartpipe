"""Shared HTTP plumbing for provider adapters."""

from __future__ import annotations

import httpx

__all__ = ["DEFAULT_TIMEOUT", "is_retryable_http", "make_client"]

# Generous read timeout — local models can take a while per item (plan/architecture.md).
DEFAULT_TIMEOUT = httpx.Timeout(connect=5.0, read=120.0, write=30.0, pool=5.0)


def make_client() -> httpx.AsyncClient:
    return httpx.AsyncClient(timeout=DEFAULT_TIMEOUT)


def is_retryable_http(exc: Exception) -> bool:
    if isinstance(exc, httpx.HTTPStatusError):
        status = exc.response.status_code
        return status == 429 or status >= 500
    return isinstance(
        exc,
        httpx.ConnectError
        | httpx.ReadTimeout
        | httpx.WriteError
        | httpx.RemoteProtocolError
        | httpx.PoolTimeout,
    )
