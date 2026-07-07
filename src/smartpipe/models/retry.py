"""Backoff for transient provider failures (429/5xx/connection drops).

The sleep and jitter sources are injectable so tests run on a fake clock.
"""

from __future__ import annotations

import asyncio
import random
from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = ["RetryPolicy", "with_retries"]

T = TypeVar("T")

# Ceiling for a server-supplied delay hint (Retry-After). max_delay keeps governing
# only the computed backoff — a server may legitimately ask for more than 8 s;
# 60 s is where a request stops being a hint and starts being abuse.
_HINT_CEILING_SECONDS = 60.0


@dataclass(frozen=True, slots=True)
class RetryPolicy:
    attempts: int = 3
    base_delay: float = 0.5
    max_delay: float = 8.0

    def __post_init__(self) -> None:
        if self.attempts < 1:
            raise ValueError(f"RetryPolicy.attempts must be >= 1, got {self.attempts}")


async def _default_sleep(seconds: float) -> None:
    await asyncio.sleep(seconds)


async def with_retries(
    policy: RetryPolicy,
    operation: Callable[[], Awaitable[T]],
    *,
    is_retryable: Callable[[Exception], bool],
    sleep: Callable[[float], Awaitable[None]] | None = None,
    rand: Callable[[], float] | None = None,
    delay_hint: Callable[[Exception], float | None] | None = None,
) -> T:
    """Retry with jittered exponential backoff, unless the failure carries its own
    delay (``delay_hint``, e.g. a ``Retry-After`` header) — the server's number is
    authoritative: used as-is, no jitter, capped only by the 60 s abuse ceiling."""
    do_sleep = _default_sleep if sleep is None else sleep
    do_rand = random.random if rand is None else rand
    for attempt in range(1, policy.attempts + 1):
        try:
            return await operation()
        except Exception as exc:
            if attempt == policy.attempts or not is_retryable(exc):
                raise
            hinted = delay_hint(exc) if delay_hint is not None else None
            if hinted is not None:
                await do_sleep(min(hinted, _HINT_CEILING_SECONDS))
                continue
            raw = policy.base_delay * 2 ** (attempt - 1)
            # jitter in [0.5x, 1.5x), then a HARD cap: max_delay bounds the
            # actual wait, so jitter can never push the sleep above it.
            await do_sleep(min(raw * (0.5 + do_rand()), policy.max_delay))
    raise AssertionError("unreachable: the loop always returns or raises")  # pragma: no cover
