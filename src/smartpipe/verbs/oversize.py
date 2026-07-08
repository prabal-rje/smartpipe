"""Auto-chunk orchestration (D26 v2): oversized items are HANDLED, not skipped.

The sin was silent chunking; the fix is LOUD automatic handling, shared by
map, extend, filter, and join's judge:

- plain map/extend: split to budget-sized chunks → the SAME instruction per
  chunk → ONE combine call (recursive, reduce-tree style, when the partial
  answers themselves overflow).
- braces/schema map/extend: extract per chunk → ONE merge call against the
  same schema.
- filter / join judge: chunk-wise judgment with ANY-true (OR) semantics and
  early exit on the first matching chunk.

Disclosure BEFORE spend: one note per oversized row names the plan (chunk
count + the synthesis call). Every chunk call flows through the metered and
budgeted model, so receipts and ``--max-calls`` count them all. ``--whole``
restores the old refusal for reproducibility purists.

Machine-cut bisection (item 3): a chunk the provider still rejects with a
context-length 400 splits in half and retries, bounded depth, disclosed.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError
from smartpipe.engine.chunking import estimate_tokens, split_text
from smartpipe.engine.prompts import (
    build_combine_request,
    build_map_request,
    build_merge_request,
    build_repair_request,
)
from smartpipe.engine.schema import validate_and_coerce
from smartpipe.io import diagnostics
from smartpipe.io.items import describe_source

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from smartpipe.engine.prompts import MapPlan
    from smartpipe.io.items import Item
    from smartpipe.models.base import ChatModel, MediaData
    from smartpipe.verbs.common import Oversize

__all__ = [
    "judge_any",
    "judge_note",
    "matched_note",
    "refusal",
    "transform_note",
    "transform_oversized",
]


def refusal(estimate: int, model_name: str, budget: int) -> str:
    """The old D26 refusal — now the ``--whole`` error (and the honest answer
    when media alone exceeds the window: bytes can't be text-chunked)."""
    return (
        f"~{estimate:,} tokens is past {model_name}'s "
        f"~{budget:,}-token budget — split it first: "
        'smartpipe split FILE | smartpipe map "..." | smartpipe reduce "..."'
    )


# --- the disclosure lines (pinned format — golden-tested) -------------------------


def transform_note(where: str, estimate: int, chunks: int, *, structured: bool) -> str:
    final = "merge" if structured else "combine"
    return f"{where} ~{estimate:,} tokens over budget - {chunks} chunks + 1 {final} call"


def judge_note(where: str, estimate: int, chunks: int) -> str:
    return f"{where} ~{estimate:,} tokens over budget - {chunks} chunks, any-true judge"


def matched_note(where: str, position: int, chunks: int) -> str:
    return f"{where}: matched in chunk {position}/{chunks}"


# --- map/extend: split → per-chunk call → one synthesis call ----------------------


async def transform_oversized(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    item: Item,
    over: Oversize,
    *,
    keep_invalid: bool = False,
) -> str | Mapping[str, object]:
    """One oversized item through the auto-chunk ladder. The item's media (if
    any) rides the FIRST chunk only — bytes can't be split, and re-sending
    them per chunk would multiply their cost silently."""
    where = describe_source(item.source)
    text_budget = over.budget - over.media_tokens
    if text_budget <= 0:  # media alone exceeds the window — nothing to chunk
        raise ItemError(refusal(over.estimate, model.ref.name, over.budget))
    chunks = split_text(item.text, text_budget)
    assert len(chunks) > 1, "an oversized item always cuts into at least two chunks"
    structured = plan.schema is not None
    diagnostics.note(transform_note(where, over.estimate, len(chunks), structured=structured))
    if structured:
        records = [
            await _extract_chunk(model, plan, instruction, chunk, _chunk_media(item, position))
            for position, chunk in enumerate(chunks)
        ]
        return await _merge(model, plan, instruction, records, keep_invalid=keep_invalid)
    partials = [
        await _plain_chunk(model, plan, instruction, chunk, _chunk_media(item, position))
        for position, chunk in enumerate(chunks)
    ]
    return await _combine(model, instruction, partials, over.budget)


def _chunk_media(item: Item, position: int) -> tuple[MediaData, ...]:
    return item.media if position == 0 else ()


async def _plain_chunk(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    chunk: str,
    media: tuple[MediaData, ...],
) -> str:
    reply = await model.complete(build_map_request(plan, instruction, chunk, media=media))
    return reply.rstrip()


async def _extract_chunk(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    chunk: str,
    media: tuple[MediaData, ...],
) -> Mapping[str, object]:
    """Per-chunk extraction with the standard single repair; a second failure
    is a per-item error (chunk partials never become --keep-invalid rows —
    only the merged record can)."""
    assert plan.schema is not None
    request = build_map_request(plan, instruction, chunk, media=media)
    reply = await model.complete(request)
    try:
        return validate_and_coerce(reply, plan.schema)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        return validate_and_coerce(await model.complete(repair), plan.schema)


async def _merge(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    records: Sequence[Mapping[str, object]],
    *,
    keep_invalid: bool,
) -> Mapping[str, object]:
    """ONE final merge call against the same schema."""
    assert plan.schema is not None
    request = build_merge_request(instruction, records, plan.schema)
    reply = await model.complete(request)
    try:
        return validate_and_coerce(reply, plan.schema)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        repaired = await model.complete(repair)
        try:
            return validate_and_coerce(repaired, plan.schema)
        except ItemError as second_error:
            if not keep_invalid:
                raise
            from smartpipe.verbs.map import invalid_row  # local: map imports this module

            return invalid_row(error=str(second_error), raw=repaired)


async def _combine(model: ChatModel, instruction: str, partials: Sequence[str], budget: int) -> str:
    """The synthesis call — recursive, reduce-tree style, when the combine
    input itself overflows the budget (each level folds groups of partials
    with the same instruction until one call can hold them)."""
    texts = list(partials)
    while not _fits(texts, budget):
        from smartpipe.engine.chunking import chunk_indices

        groups = chunk_indices([estimate_tokens(text) for text in texts], budget)
        if len(groups) >= len(texts):
            break  # singleton groups: another level cannot shrink the input
        texts = [
            texts[group[0]]  # a lone partial passes through unfolded — no wasted call
            if len(group) == 1
            else await _combine_call(model, instruction, [texts[i] for i in group])
            for group in groups
        ]
    return await _combine_call(model, instruction, texts)


def _fits(texts: Sequence[str], budget: int) -> bool:
    return sum(estimate_tokens(text) for text in texts) <= budget


async def _combine_call(model: ChatModel, instruction: str, partials: Sequence[str]) -> str:
    reply = await model.complete(build_combine_request(instruction, partials))
    return reply.rstrip()


# --- filter / join: ANY-true chunk judgment with early exit -----------------------


async def judge_any(
    chunks: Sequence[str],
    judge: Callable[[str], Awaitable[bool]],
    *,
    where: str,
    estimate: int,
) -> bool:
    """OR the chunk verdicts, stopping at the first true one (cost win); the
    plan is noted before the first call, the matching chunk after."""
    diagnostics.note(judge_note(where, estimate, len(chunks)))
    for position, chunk in enumerate(chunks, start=1):
        if await judge(chunk):
            diagnostics.note(matched_note(where, position, len(chunks)))
            return True
    return False
