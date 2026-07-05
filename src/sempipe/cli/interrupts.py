"""Graceful Ctrl-C for per-item verbs (ux.md §12).

First SIGINT sets the ``stop`` event — the runner stops intake and drains in-flight
work; a watchdog caps the drain. Second SIGINT hard-exits 130 immediately (the user
means it). Whole-set verbs don't enter this context and keep the immediate
KeyboardInterrupt → 130 path — no partial result exists for them.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode
from sempipe.io import diagnostics

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator

    from sempipe.models.budget import CallBudget

__all__ = [
    "drain_cap",
    "graceful_interrupts",
    "settle_budget",
]

_DEFAULT_DRAIN_SECONDS = 10.0


def drain_cap() -> float:
    # SEMPIPE_DRAIN_SECONDS is a test seam (the drain-timeout e2e would otherwise
    # take the full 10 s); not documented as public surface.
    raw = os.environ.get("SEMPIPE_DRAIN_SECONDS", "").strip()
    try:
        return float(raw) if raw else _DEFAULT_DRAIN_SECONDS
    except ValueError:
        return _DEFAULT_DRAIN_SECONDS


async def _watchdog(cap: float, task: asyncio.Task[object]) -> None:
    await asyncio.sleep(cap)
    diagnostics.drain_timed_out()
    task.cancel()


@asynccontextmanager
async def graceful_interrupts() -> AsyncGenerator[asyncio.Event]:
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None  # always inside asyncio.run here
    sigints = 0
    watchdog: asyncio.Task[None] | None = None

    def on_sigint() -> None:
        nonlocal sigints, watchdog
        sigints += 1
        if sigints == 1:
            stop.set()
            watchdog = loop.create_task(_watchdog(drain_cap(), task))
        else:  # the user means it — flush what we can and go
            with contextlib.suppress(Exception):
                sys.stdout.flush()
                sys.stderr.flush()
            os._exit(int(ExitCode.INTERRUPTED))

    try:
        loop.add_signal_handler(signal.SIGINT, on_sigint)
    except (NotImplementedError, RuntimeError):  # pragma: no cover — Windows loop
        yield stop  # never set: falls back to KeyboardInterrupt → 130 (documented)
        return
    try:
        yield stop
    finally:
        loop.remove_signal_handler(signal.SIGINT)
        if watchdog is not None:
            watchdog.cancel()


def settle_budget(budget: CallBudget | None, code: ExitCode) -> ExitCode:
    """D18: a run whose --max-calls budget fired never exits 0 — completeness
    can't be trusted; the note names the cause after the drain summary."""
    if budget is None or not budget.exhausted:
        return code
    diagnostics.note(f"stopped by --max-calls ({budget.calls} calls made)")
    return ExitCode.PARTIAL if code is ExitCode.OK else code
