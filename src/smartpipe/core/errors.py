"""Exit codes and the error taxonomy.

Contract: plan/decisions.md D12 and plan/architecture.md "Error taxonomy".
"""

from __future__ import annotations

from enum import IntEnum

__all__ = [
    "ExitCode",
    "ItemError",
    "SempipeError",
    "SetupFault",
    "TooManyFailures",
    "UsageFault",
]


class ExitCode(IntEnum):
    OK = 0
    PARTIAL = 1
    SETUP = 2
    ALL_FAILED = 3
    USAGE = 64
    BUG = 70
    INTERRUPTED = 130
    PIPE_CLOSED = 141  # 128 + SIGPIPE: downstream closed the pipe (| head) — die silently


class SempipeError(Exception):
    """Base of all *expected* smartpipe failures. Never raised directly."""


class UsageFault(SempipeError):
    """Bad flags/arguments/grammar misuse → exit 64."""


class SetupFault(SempipeError):
    """No model, unreachable endpoint, missing key/extra, broken config → exit 2.

    The message may be a full multi-line screen from plan/ux.md.
    """


class ItemError(SempipeError):
    """One item failed; the runner turns this into a skip-and-warn, never a crash."""


class TooManyFailures(SempipeError):
    """The failure policy tripped (>50 % of items failed) → exit 3."""

    def __init__(self, failed: int, total: int, last_reason: str) -> None:
        self.failed = failed
        self.total = total
        self.last_reason = last_reason
        super().__init__(f"stopping — {failed} of {total} items failed the same way")
