"""Shared verb helpers: outcome→exit-code and materializing an item stream."""

from __future__ import annotations

from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence

    from sempipe.io.items import Item

__all__ = ["aiter_items", "interrupted_exit_code", "outcome_exit_code", "prepend"]


def outcome_exit_code(*, done: int, skipped: int) -> ExitCode:
    """0 = all ok · 1 = some skipped · 3 = every item failed (spec §12)."""
    if skipped == 0:
        return ExitCode.OK
    if done == 0:
        return ExitCode.ALL_FAILED
    return ExitCode.PARTIAL


def interrupted_exit_code(*, done: int, skipped: int) -> ExitCode:
    """After a drained Ctrl-C (ux.md §12): the run's normal outcome code — an
    interrupt doesn't mask partiality — except 130 when nothing finished at all."""
    if done == 0 and skipped == 0:
        return ExitCode.INTERRUPTED
    return outcome_exit_code(done=done, skipped=skipped)


async def aiter_items(items: Sequence[Item]) -> AsyncIterator[Item]:
    for item in items:
        yield item


async def prepend(first: Item, rest: AsyncIterator[Item]) -> AsyncIterator[Item]:
    """Re-attach an item pulled for a first-item check (filter's brace fail-fast)."""
    yield first
    async for item in rest:
        yield item
