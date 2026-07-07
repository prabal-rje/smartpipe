"""In-process coverage for the interrupt shell (the hard-exit path is e2e-only)."""

from __future__ import annotations

import asyncio
import os
import signal
import sys

import pytest

from smartpipe.cli.interrupts import drain_cap, graceful_interrupts

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


async def test_first_sigint_sets_stop() -> None:
    async with graceful_interrupts() as stop:
        assert not stop.is_set()
        os.kill(os.getpid(), signal.SIGINT)  # handled by our handler, not raised
        await asyncio.wait_for(stop.wait(), timeout=2)
    # context exit restored the default handler and cancelled the watchdog


async def test_drain_timeout_cancels_the_task(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setenv("SMARTPIPE_DRAIN_SECONDS", "0.05")
    with pytest.raises(asyncio.CancelledError):
        async with graceful_interrupts() as stop:
            os.kill(os.getpid(), signal.SIGINT)
            await asyncio.wait_for(stop.wait(), timeout=2)
            await asyncio.sleep(5)  # "drain" that overruns the cap → watchdog cancels
    assert "drain timed out" in capsys.readouterr().err
