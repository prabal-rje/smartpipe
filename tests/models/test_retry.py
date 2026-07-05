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


async def test_delay_is_capped_before_jitter() -> None:
    recorder = _Recorder()

    async def operation() -> str:
        recorder.calls += 1
        raise _TransientError

    with pytest.raises(_TransientError):
        await with_retries(
            RetryPolicy(attempts=3, base_delay=4.0, max_delay=5.0),
            operation,
            is_retryable=_is_transient,
            sleep=recorder.sleep,
            rand=lambda: 0.5,
        )
    assert recorder.sleeps == [4.0, 5.0]  # 8.0 capped to 5.0
