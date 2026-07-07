from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smartpipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable


class _TransientError(Exception):
    pass


class _FatalError(Exception):
    pass


def _is_transient(exc: Exception) -> bool:
    return isinstance(exc, _TransientError)


class _Recorder:
    def __init__(self) -> None:
        self.sleeps: list[float] = []
        self.calls = 0

    async def sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)


async def test_retries_then_succeeds() -> None:
    recorder = _Recorder()

    async def operation() -> str:
        recorder.calls += 1
        if recorder.calls < 3:
            raise _TransientError
        return "ok"

    result = await with_retries(
        RetryPolicy(attempts=3, base_delay=1.0),
        operation,
        is_retryable=_is_transient,
        sleep=recorder.sleep,
        rand=lambda: 0.5,  # jitter factor becomes exactly 1.0
    )
    assert result == "ok"
    assert recorder.calls == 3
    assert recorder.sleeps == [1.0, 2.0]  # exponential


async def test_gives_up_after_the_last_attempt() -> None:
    recorder = _Recorder()

    async def operation() -> str:
        recorder.calls += 1
        raise _TransientError

    with pytest.raises(_TransientError):
        await with_retries(
            RetryPolicy(attempts=3, base_delay=1.0),
            operation,
            is_retryable=_is_transient,
            sleep=recorder.sleep,
            rand=lambda: 0.5,
        )
    assert recorder.calls == 3
    assert recorder.sleeps == [1.0, 2.0]  # no sleep after the final failure


async def test_non_retryable_raises_immediately() -> None:
    recorder = _Recorder()

    async def operation() -> str:
        recorder.calls += 1
        raise _FatalError

    with pytest.raises(_FatalError):
        await with_retries(
            RetryPolicy(attempts=3, base_delay=1.0),
            operation,
            is_retryable=_is_transient,
            sleep=recorder.sleep,
            rand=lambda: 0.5,
        )
    assert recorder.calls == 1
    assert recorder.sleeps == []


async def test_max_delay_is_a_hard_cap_even_under_full_jitter() -> None:
    # regression (adversarial review): jitter is applied BEFORE the cap, so the
    # actual sleep can never exceed max_delay. rand=0.99 => jitter factor ~1.49.
    recorder = _Recorder()

    async def operation() -> str:
        recorder.calls += 1
        raise _TransientError

    with pytest.raises(_TransientError):
        await with_retries(
            RetryPolicy(attempts=4, base_delay=4.0, max_delay=5.0),
            operation,
            is_retryable=_is_transient,
            sleep=recorder.sleep,
            rand=lambda: 0.99,
        )
    assert all(delay <= 5.0 for delay in recorder.sleeps), recorder.sleeps
    assert max(recorder.sleeps) == pytest.approx(5.0)  # the cap is actually reached


def test_attempts_below_one_is_rejected() -> None:
    with pytest.raises(ValueError, match="attempts must be >= 1"):
        RetryPolicy(attempts=0)


# --- Retry-After: the server's number is authoritative (workstream 06) ----------


async def _fail_once_then(recorder: _Recorder, result: str) -> Callable[[], Awaitable[str]]:
    async def operation() -> str:
        recorder.calls += 1
        if recorder.calls < 2:
            raise _TransientError
        return result

    return operation


async def test_delay_hint_wins_over_jitter_exactly() -> None:
    recorder = _Recorder()
    result = await with_retries(
        RetryPolicy(attempts=3, base_delay=1.0),
        await _fail_once_then(recorder, "ok"),
        is_retryable=_is_transient,
        sleep=recorder.sleep,
        rand=lambda: 0.99,  # jitter would give ~1.49 — must not be used
        delay_hint=lambda exc: 3.0,
    )
    assert result == "ok"
    assert recorder.sleeps == [3.0]  # the server's number, no jitter


async def test_hostile_hint_is_clamped_to_the_ceiling() -> None:
    recorder = _Recorder()
    await with_retries(
        RetryPolicy(attempts=3, base_delay=1.0),
        await _fail_once_then(recorder, "ok"),
        is_retryable=_is_transient,
        sleep=recorder.sleep,
        rand=lambda: 0.5,
        delay_hint=lambda exc: 86400.0,
    )
    assert recorder.sleeps == [60.0]  # pinned abuse ceiling


async def test_hint_none_falls_back_to_jittered_backoff() -> None:
    recorder = _Recorder()
    await with_retries(
        RetryPolicy(attempts=3, base_delay=1.0),
        await _fail_once_then(recorder, "ok"),
        is_retryable=_is_transient,
        sleep=recorder.sleep,
        rand=lambda: 0.5,  # jitter factor exactly 1.0
        delay_hint=lambda exc: None,
    )
    assert recorder.sleeps == [1.0]  # the regression guard: computed path unchanged


async def test_hint_may_exceed_max_delay_below_the_ceiling() -> None:
    # max_delay governs only the computed path — the server may legitimately
    # ask for more than 8 s; 60 s is the abuse ceiling.
    recorder = _Recorder()
    await with_retries(
        RetryPolicy(attempts=3, base_delay=1.0, max_delay=8.0),
        await _fail_once_then(recorder, "ok"),
        is_retryable=_is_transient,
        sleep=recorder.sleep,
        rand=lambda: 0.5,
        delay_hint=lambda exc: 30.0,
    )
    assert recorder.sleeps == [30.0]
