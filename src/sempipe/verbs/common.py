"""Shared verb helpers: outcome→exit-code and materializing an item stream."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sempipe.io.items import Item

__all__ = ["aiter_items", "outcome_exit_code"]


def outcome_exit_code(*, done: int, skipped: int) -> ExitCode:
    """0 = all ok · 1 = some skipped · 3 = every item failed (spec §12)."""
    if skipped == 0:
        return ExitCode.OK
    if done == 0:
        return ExitCode.ALL_FAILED
    return ExitCode.PARTIAL


async def aiter_items(items: Sequence[Item]) -> AsyncIterator[Item]:
    for item in items:
        yield item
