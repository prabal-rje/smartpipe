from __future__ import annotations

import pytest

from sempipe.models.retry import RetryPolicy, with_retries


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
