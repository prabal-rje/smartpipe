"""Regression tests for the retryable-error classifier.

Root cause found by the Stage 2 adversarial review: httpx's ``ConnectTimeout``
and ``WriteTimeout`` subclass ``TimeoutException``, NOT ``ConnectError`` /
``WriteError`` — so the original enumerated tuple silently excluded them, and
real timeouts were never retried despite every adapter docstring promising they
were. The classifier now keys off ``TimeoutException`` to catch all four.
"""

from __future__ import annotations

import httpx
import pytest

from sempipe.models.http_support import is_retryable_http


@pytest.mark.parametrize(
    "exc",
    [
        httpx.ConnectTimeout("slow connect"),
        httpx.ReadTimeout("slow read"),
        httpx.WriteTimeout("slow write"),
        httpx.PoolTimeout("no pool slot"),
        httpx.ConnectError("refused"),
        httpx.RemoteProtocolError("bad framing"),
        httpx.WriteError("write failed"),
    ],
)
def test_transient_transport_errors_are_retryable(exc: httpx.HTTPError) -> None:
    assert is_retryable_http(exc) is True


@pytest.mark.parametrize("status", [429, 500, 502, 503, 529])
def test_server_and_rate_limit_statuses_retryable(status: int) -> None:
    response = httpx.Response(status, request=httpx.Request("POST", "http://x"))
    error = httpx.HTTPStatusError("e", request=response.request, response=response)
    assert is_retryable_http(error) is True


@pytest.mark.parametrize("status", [400, 401, 403, 404, 422])
def test_client_errors_are_not_retryable(status: int) -> None:
    response = httpx.Response(status, request=httpx.Request("POST", "http://x"))
    assert not is_retryable_http(
        httpx.HTTPStatusError("e", request=response.request, response=response)
    )


def test_unrelated_exception_is_not_retryable() -> None:
    assert is_retryable_http(ValueError("nope")) is False
