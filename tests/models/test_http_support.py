"""Regression tests for the retryable-error classifier.

Root cause found by the Stage 2 adversarial review: httpx's ``ConnectTimeout``
and ``WriteTimeout`` subclass ``TimeoutException``, NOT ``ConnectError`` /
``WriteError`` — so the original enumerated tuple silently excluded them, and
real timeouts were never retried despite every adapter docstring promising they
were. The classifier now keys off ``TimeoutException`` to catch all four.
"""

from __future__ import annotations

from datetime import UTC, datetime
from email.utils import format_datetime
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.models.http_support import is_retryable_http, retry_after_seconds
from smartpipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    import respx


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


# --- Retry-After parsing (workstream 06) -----------------------------------------


def _status_error(status: int, headers: dict[str, str] | None = None) -> httpx.HTTPStatusError:
    request = httpx.Request("POST", "http://x")
    response = httpx.Response(status, headers=headers or {}, request=request)
    return httpx.HTTPStatusError("e", request=request, response=response)


def test_integer_seconds_parse_exactly() -> None:
    assert retry_after_seconds(_status_error(429, {"Retry-After": "3"})) == 3.0


def test_zero_seconds_is_a_valid_hint() -> None:
    assert retry_after_seconds(_status_error(429, {"Retry-After": "0"})) == 0.0


def test_http_date_measures_from_the_injected_clock() -> None:
    base = 1_750_000_000.0
    header = format_datetime(datetime.fromtimestamp(base + 4, tz=UTC), usegmt=True)
    error = _status_error(429, {"Retry-After": header})
    assert retry_after_seconds(error, now=lambda: base) == pytest.approx(4.0)


def test_http_date_in_the_past_is_none() -> None:
    base = 1_750_000_000.0
    header = format_datetime(datetime.fromtimestamp(base - 30, tz=UTC), usegmt=True)
    error = _status_error(429, {"Retry-After": header})
    assert retry_after_seconds(error, now=lambda: base) is None


def test_negative_seconds_is_none() -> None:
    assert retry_after_seconds(_status_error(429, {"Retry-After": "-5"})) is None


def test_garbage_is_none() -> None:
    assert retry_after_seconds(_status_error(429, {"Retry-After": "soon"})) is None


def test_absent_header_is_none() -> None:
    assert retry_after_seconds(_status_error(429)) is None


def test_non_http_status_error_is_none() -> None:
    assert retry_after_seconds(ValueError("nope")) is None


async def test_retry_after_flows_from_wire_to_sleep(respx_mock: respx.MockRouter) -> None:
    # the plan's step-3 integration: 429 + header, then 200 — one retry, the
    # recorded sleep is the server's number, and the result comes through.
    respx_mock.post("http://x/api").side_effect = [
        httpx.Response(429, headers={"Retry-After": "3"}),
        httpx.Response(200, json={"ok": True}),
    ]
    sleeps: list[float] = []

    async def record(seconds: float) -> None:
        sleeps.append(seconds)

    async with httpx.AsyncClient() as client:

        async def attempt() -> object:
            response = await client.post("http://x/api")
            response.raise_for_status()
            return response.json()

        result = await with_retries(
            RetryPolicy(attempts=3),
            attempt,
            is_retryable=is_retryable_http,
            sleep=record,
            delay_hint=retry_after_seconds,
        )
    assert result == {"ok": True}
    assert sleeps == [3.0]
