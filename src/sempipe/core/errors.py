"""Exit codes and the error taxonomy.

Contract: plan/decisions.md D12 and plan/architecture.md "Error taxonomy".
"""

from __future__ import annotations

from enum import IntEnum

__all__ = ["ExitCode"]


class ExitCode(IntEnum):
    OK = 0
    PARTIAL = 1
    SETUP = 2
    ALL_FAILED = 3
    USAGE = 64
    BUG = 70
    INTERRUPTED = 130
