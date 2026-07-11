"""Graceful Ctrl-C for per-item verbs (ux.md §12).

First SIGINT sets the ``stop`` event — the runner stops intake and drains in-flight
work; a watchdog caps the drain. Second SIGINT hard-exits 130 immediately (the user
means it). Whole-set verbs don't enter this context and keep the immediate
KeyboardInterrupt → 130 path — no partial result exists for them.

The handler is installed with :func:`signal.signal`, not ``loop.add_signal_handler``:
a raw C-level handler runs between bytecodes on the MAIN thread, so it fires during
pure-Python compute (a CPU-bound fold, B1) and interrupts blocking syscalls — exactly
when a loop-callback handler would be starved. The synchronous source of truth is a
``threading.Event`` any worker thread (or a fold callback) reads through
:func:`stop_requested`; the run's ``asyncio.Event`` is set alongside it for async
consumers. A drain that overruns the cap is cancelled, then — because
``asyncio.run``'s teardown JOINS running executor threads and a stuck to-thread call
(mid-inference, an uncooperative fold) can hold that join far past the cap — an
off-loop daemon takes the hard-exit path itself after a short grace.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import sys
import threading
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, NoReturn

from smartpipe.core.errors import ExitCode
from smartpipe.io import diagnostics

if TYPE_CHECKING:
    from collections.abc import AsyncGenerator, Callable
    from types import FrameType

    from smartpipe.models.budget import CallBudget

__all__ = [
    "drain_cap",
    "drain_grace",
    "graceful_interrupts",
    "register_hard_exit_cleanup",
    "settle_budget",
    "stand_down_hard_exit",
    "stop_requested",
]

_DEFAULT_DRAIN_SECONDS = 10.0
_DEFAULT_GRACE_SECONDS = 2.0

_FIRST_ACK = b"\nstopping - draining; press Ctrl-C again to exit now\n"
_SECOND_ACK = b"\nexiting now\n"
_ESCALATION_ACK = b"drain timed out - exiting\n"

# Run-scoped module state — the documented exception to no-globals, like
# ``io/metering`` and ``io/manifest``: one verb per process, reset when the
# context is (re-)entered. The signal handler is inherently process-global, so
# the drain flag, the hard-exit cleanup registry, and the escalation disarm live
# here where ``stop_requested``/``register_hard_exit_cleanup`` can reach them
# without threading a handle through every async consumer.
_sync_stop = threading.Event()
_hard_exit_cleanups: list[Callable[[], None]] = []
_escalation_disarm: threading.Event | None = None


def stop_requested() -> bool:
    """Whether a first Ctrl-C has been seen — readable synchronously from any
    worker thread or fold callback (B1). Backed by a ``threading.Event`` the
    signal handler sets in place, so it is true the instant the handler runs,
    ahead of the loop scheduling the async ``stop``."""
    return _sync_stop.is_set()


def register_hard_exit_cleanup(cleanup: Callable[[], None]) -> None:
    """Register a callback to run just before a hard exit (second Ctrl-C or the
    watchdog escalation) — the seam B6 uses to unlink the manifest's reserved
    temp before ``os._exit`` bypasses every Python-level unwind. Cleanups run
    best-effort, each shielded, in registration order."""
    _hard_exit_cleanups.append(cleanup)


def stand_down_hard_exit() -> None:
    """Disarm a pending watchdog escalation. The CLI's ``asyncio.run`` reaps the
    daemon on a clean exit for free, so production never needs this; it is the
    seam an in-process test uses after triggering the watchdog, so the daemon
    does not fire ``os._exit`` under the test runner."""
    if _escalation_disarm is not None:
        _escalation_disarm.set()


def _reset_interrupt_state() -> None:
    global _escalation_disarm
    if _escalation_disarm is not None:
        _escalation_disarm.set()  # stand down any escalation left by a prior scope
    _escalation_disarm = None
    _sync_stop.clear()
    _hard_exit_cleanups.clear()


def drain_cap() -> float:
    # SMARTPIPE_DRAIN_SECONDS is a test seam (the drain-timeout e2e would otherwise
    # take the full 10 s); not documented as public surface.
    return _seconds_env("SMARTPIPE_DRAIN_SECONDS", _DEFAULT_DRAIN_SECONDS)


def drain_grace() -> float:
    # SMARTPIPE_DRAIN_GRACE_SECONDS is the twin test seam for the watchdog's
    # post-cancel grace before it takes the hard-exit path itself.
    return _seconds_env("SMARTPIPE_DRAIN_GRACE_SECONDS", _DEFAULT_GRACE_SECONDS)


def _seconds_env(name: str, default: float) -> float:
    raw = os.environ.get(name, "").strip()
    try:
        return float(raw) if raw else default
    except ValueError:
        return default


def _run_hard_exit_cleanups() -> None:
    """Run every registered hard-exit cleanup (B6), each shielded, in order."""
    for cleanup in tuple(_hard_exit_cleanups):
        with contextlib.suppress(Exception):
            cleanup()


def _hard_exit(ack: bytes) -> NoReturn:  # pragma: no cover — os._exit path, exercised e2e
    """The shared hard-exit path — second Ctrl-C or watchdog escalation. Writes a
    direct acknowledgment to fd 2, runs the registered cleanups (B6), flushes,
    then ``os._exit`` so a starved loop can never delay it."""
    with contextlib.suppress(Exception):
        os.write(2, ack)
    _run_hard_exit_cleanups()
    with contextlib.suppress(Exception):
        sys.stdout.flush()
        sys.stderr.flush()
    os._exit(int(ExitCode.INTERRUPTED))


def _escalation_worker(grace: float, disarm: threading.Event) -> None:
    """Off-loop escape: after the watchdog cancels the drain, a cancellable run
    unwinds and the process exits (this daemon is reaped, never fires); a run
    stuck in an executor thread hangs ``asyncio.run``'s join, so after ``grace``
    with no stand-down this takes the hard-exit path itself."""
    if disarm.wait(grace):
        return  # the drain settled (or a test stood us down) before the grace elapsed
    _hard_exit(_ESCALATION_ACK)


async def _watchdog(cap: float, grace: float, task: asyncio.Task[object]) -> None:
    await asyncio.sleep(cap)
    diagnostics.drain_timed_out()
    task.cancel()
    _arm_escalation(grace)


def _arm_escalation(grace: float) -> None:
    global _escalation_disarm
    disarm = threading.Event()
    _escalation_disarm = disarm
    threading.Thread(
        target=_escalation_worker,
        args=(grace, disarm),
        name="smartpipe-drain-escalation",
        daemon=True,
    ).start()


@asynccontextmanager
async def graceful_interrupts() -> AsyncGenerator[asyncio.Event]:
    _reset_interrupt_state()
    stop = asyncio.Event()
    loop = asyncio.get_running_loop()
    task = asyncio.current_task()
    assert task is not None  # always inside asyncio.run here
    sigints = 0
    watchdog: asyncio.Task[None] | None = None

    def first_press() -> None:
        # runs on the loop thread (scheduled by the handler) — safe to touch the
        # asyncio.Event and create the watchdog task here, never in the handler.
        nonlocal watchdog
        stop.set()
        if watchdog is None:
            watchdog = loop.create_task(_watchdog(drain_cap(), drain_grace(), task))

    def on_sigint(_signum: int, _frame: FrameType | None) -> None:
        nonlocal sigints
        sigints += 1
        if sigints == 1:
            _sync_stop.set()  # synchronous truth first, for fold callbacks / worker threads
            with contextlib.suppress(Exception):
                os.write(2, _FIRST_ACK)  # direct ack — never through a starved loop
            loop.call_soon_threadsafe(first_press)
        else:  # the user means it — flush what we can and go
            _hard_exit(_SECOND_ACK)

    try:
        previous = signal.signal(signal.SIGINT, on_sigint)
    except ValueError:  # pragma: no cover — not the main thread (signal.signal refuses)
        yield stop  # never set: falls back to KeyboardInterrupt → 130 (documented)
        return
    try:
        yield stop
    finally:
        # Restore the prior handler and cancel the watchdog. Deliberately do NOT
        # stand down the escalation here: a stuck-executor teardown runs this
        # finally BEFORE the join hang, so disarming would remove the only escape.
        with contextlib.suppress(ValueError):
            signal.signal(signal.SIGINT, previous)
        if watchdog is not None:
            watchdog.cancel()


def settle_budget(budget: CallBudget | None, code: ExitCode) -> ExitCode:
    """D18: a run whose --max-calls budget fired never exits 0 — completeness
    can't be trusted; the note names the cause after the drain summary. A belt
    may set the shared stop before the first source item starts (for example a
    streaming ranker's query call), so its synthetic 130 is a partial budget
    stop, not a user interrupt."""
    if budget is None or not budget.exhausted:
        return code
    diagnostics.note(f"stopped by --max-calls ({budget.describe_usage()})")
    return ExitCode.PARTIAL if code in (ExitCode.OK, ExitCode.INTERRUPTED) else code
