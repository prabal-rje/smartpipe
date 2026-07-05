"""Shared verb helpers: outcome→exit-code, item-stream plumbing, embed batching."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

from sempipe.core.errors import ExitCode, ItemError, TooManyFailures
from sempipe.engine.runner import (
    Done,
    FailurePolicy,
    ItemOutcome,
    Skipped,
    should_halt,
    should_halt_consecutive,
)

if TYPE_CHECKING:
    import asyncio
    from collections.abc import AsyncIterator, Iterator, Sequence

    from sempipe.io.items import Item
    from sempipe.models.base import EmbeddingModel

__all__ = [
    "EMBED_BATCH_SIZE",
    "aiter_items",
    "batched",
    "embed_in_batches",
    "ensure_text_item",
    "interrupted_exit_code",
    "outcome_exit_code",
    "prepend",
]

T = TypeVar("T")

EMBED_BATCH_SIZE = 64  # texts per embed call on finite corpora (plan/post-1.0/06)


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


def ensure_text_item(item: Item) -> None:
    """Non-map verbs read text; an image item is skipped with a pointer, not a crash."""
    if item.image is not None:
        raise ItemError("image items need map — this verb reads text")


def batched(items: Sequence[T], size: int) -> Iterator[tuple[T, ...]]:
    """``itertools.batched`` for the 3.11 floor — tuple chunks, order preserved."""
    if size < 1:
        raise ValueError(f"batch size must be >= 1, got {size}")
    return (tuple(items[start : start + size]) for start in range(0, len(items), size))


async def embed_in_batches(
    model: EmbeddingModel,
    items: Sequence[Item],
    *,
    failure_policy: FailurePolicy,
    batch_size: int = EMBED_BATCH_SIZE,
    stop: asyncio.Event | None = None,
) -> AsyncIterator[ItemOutcome[tuple[Item, tuple[float, ...]]]]:
    """Embed a finite corpus in ≤``batch_size`` chunks, sequentially (DEFER-3).

    Bypasses ``run_ordered`` on purpose — batching ≠ per-item workers: order
    comes from sequential chunks, and isolation from the fallback (a failed
    chunk re-runs item-by-item, so one poison item skips alone instead of
    taking 63 neighbors with it). Accounting mirrors the runner's: majority
    failure past ``min_sample`` halts with ``TooManyFailures``.
    """
    processed = 0
    skipped = 0
    consecutive = 0
    succeeded = False

    def account_skip(reason: str) -> None:
        nonlocal skipped, consecutive
        skipped += 1
        consecutive += 1
        if should_halt(failure_policy, total=processed, skipped=skipped):
            raise TooManyFailures(skipped, processed, reason)
        if should_halt_consecutive(failure_policy, succeeded=succeeded, consecutive=consecutive):
            raise TooManyFailures(skipped, processed, reason)

    def account_done() -> None:
        nonlocal consecutive, succeeded
        consecutive = 0
        succeeded = True

    for chunk in batched(tuple(items), batch_size):
        if stop is not None and stop.is_set():
            return
        text_items: list[Item] = []
        for item in chunk:
            if item.image is not None:
                processed += 1
                reason = "image items need map — this verb reads text"
                yield Skipped(item.source.index, reason, item.source)
                account_skip(reason)
            else:
                text_items.append(item)
        if not text_items:
            continue
        try:
            vectors = await model.embed([item.text for item in text_items])
            if len(vectors) != len(text_items):
                raise ItemError(
                    f"endpoint returned {len(vectors)} vectors for {len(text_items)} texts"
                )
        except ItemError:
            for item in text_items:
                if stop is not None and stop.is_set():
                    return
                processed += 1
                try:
                    vector = (await model.embed([item.text]))[0]
                except ItemError as exc:
                    yield Skipped(item.source.index, str(exc), item.source)
                    account_skip(str(exc))
                else:
                    account_done()
                    yield Done(item.source.index, (item, vector))
            continue
        for item, vector in zip(text_items, vectors, strict=True):
            processed += 1
            account_done()
            yield Done(item.source.index, (item, vector))
