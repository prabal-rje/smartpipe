"""In-process coverage for the interrupt shell (the hard-exit path is e2e-only)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest

from smartpipe.cli import interrupts
from smartpipe.cli.interrupts import (
    drain_cap,
    drain_grace,
    graceful_interrupts,
    register_hard_exit_cleanup,
    stand_down_hard_exit,
    stop_requested,
)

# os.kill(pid, SIGINT) on Windows raises CTRL_C_EVENT for the whole console
# process group — it killed the CI runner's pytest outright (exit 2, one dot).
# The drain semantics under test are POSIX contracts.
pytestmark = pytest.mark.skipif(sys.platform == "win32", reason="POSIX signal delivery")


def test_drain_cap_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMARTPIPE_DRAIN_SECONDS", raising=False)
    assert drain_cap() == 10.0
    monkeypatch.setenv("SMARTPIPE_DRAIN_SECONDS", "2.5")
    assert drain_cap() == 2.5
    monkeypatch.setenv("SMARTPIPE_DRAIN_SECONDS", "garbage")
    assert drain_cap() == 10.0  # invalid → default, never a crash


def test_drain_grace_env_parsing(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("SMARTPIPE_DRAIN_GRACE_SECONDS", raising=False)
    assert drain_grace() == 2.0
    monkeypatch.setenv("SMARTPIPE_DRAIN_GRACE_SECONDS", "0.5")
    assert drain_grace() == 0.5
    monkeypatch.setenv("SMARTPIPE_DRAIN_GRACE_SECONDS", "garbage")
    assert drain_grace() == 2.0  # invalid → default, never a crash


async def test_first_sigint_sets_both_the_sync_and_async_stop(
    capfd: pytest.CaptureFixture[str],
) -> None:
    # capfd (not capsys): the acknowledgment is written to raw fd 2 with os.write,
    # so a starved loop / buffered stream can never swallow it.
    before = signal.getsignal(signal.SIGINT)  # asyncio.run's own Runner handler here
    async with graceful_interrupts() as stop:
        assert signal.getsignal(signal.SIGINT) is not before  # our handler took over
        assert not stop.is_set()
        assert not stop_requested()  # the synchronous truth starts clear too
        os.kill(os.getpid(), signal.SIGINT)  # handled by our handler, not raised
        assert stop_requested()  # threading.Event is set IN the handler, synchronously
        await asyncio.wait_for(stop.wait(), timeout=2)  # async event catches up via the loop
    # the direct fd-2 acknowledgment landed on stderr (stdout stays sacred)
    captured = capfd.readouterr()
    assert captured.out == ""
    assert "draining" in captured.err
    # context exit restored the prior handler and cancelled the watchdog
    assert signal.getsignal(signal.SIGINT) is before


async def test_reentering_resets_the_synchronous_stop() -> None:
    async with graceful_interrupts():
        os.kill(os.getpid(), signal.SIGINT)
        assert stop_requested()
    async with graceful_interrupts():
        assert not stop_requested()  # a fresh scope starts clear


def test_run_hard_exit_cleanups_runs_registered_callbacks_shielded() -> None:
    interrupts._reset_interrupt_state()  # pyright: ignore[reportPrivateUsage] — test isolation
    ran: list[str] = []
    register_hard_exit_cleanup(lambda: ran.append("first"))
    register_hard_exit_cleanup(lambda: (_ for _ in ()).throw(OSError("boom")))  # shielded
    register_hard_exit_cleanup(lambda: ran.append("third"))
    interrupts._run_hard_exit_cleanups()  # pyright: ignore[reportPrivateUsage] — path under test
    assert ran == ["first", "third"]  # a raising cleanup never aborts the rest
    interrupts._reset_interrupt_state()  # pyright: ignore[reportPrivateUsage] — test isolation


def test_stand_down_is_a_no_op_without_an_armed_escalation() -> None:
    interrupts._reset_interrupt_state()  # pyright: ignore[reportPrivateUsage] — test isolation
    stand_down_hard_exit()  # must not raise when nothing is armed


async def test_drain_timeout_cancels_the_task(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMARTPIPE_DRAIN_SECONDS", "0.05")
    # A long grace plus an explicit stand-down keeps the escalation daemon from
    # firing os._exit under pytest: the cancellable drain never needs it.
    monkeypatch.setenv("SMARTPIPE_DRAIN_GRACE_SECONDS", "30")
    try:
        with pytest.raises(asyncio.CancelledError):
            async with graceful_interrupts() as stop:
                os.kill(os.getpid(), signal.SIGINT)
                await asyncio.wait_for(stop.wait(), timeout=2)
                await asyncio.sleep(5)  # "drain" that overruns the cap → watchdog cancels
    finally:
        stand_down_hard_exit()  # disarm the daemon the watchdog armed on cancel
    assert "drain timed out" in capsys.readouterr().err
