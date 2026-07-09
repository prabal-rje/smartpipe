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

Bisect-on-context-400 (item 3): estimates are hints, the wire's rejection is
ground truth. A MACHINE-cut text (``__source.as`` in tokens/pages/minutes/
seconds, or any auto-chunk this module made) that still draws a
context-length 400 splits in half and retries both halves — bounded depth,
disclosed once per row. USER cuts (file/lines/jsonl) never re-split: those
boundaries are the user's, and with the auto strategies above they should
never reach a 400 anyway.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, TypeVar

from smartpipe.core.errors import ItemError
from smartpipe.engine.chunking import estimate_tokens, is_context_overflow, split_text
from smartpipe.engine.prompts import (
    build_combine_request,
    build_map_request,
    build_merge_request,
    build_repair_request,
    render_input,
)
from smartpipe.engine.schema import validate_and_coerce
from smartpipe.io import diagnostics
from smartpipe.io.items import describe_source

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence

    from smartpipe.engine.prompts import MapPlan
    from smartpipe.io.items import Item, ItemSource
    from smartpipe.models.base import ChatModel, MediaData
    from smartpipe.verbs.common import Oversize

__all__ = [
    "MAX_BISECT_DEPTH",
    "RowNote",
    "judge_any",
    "judge_bisected",
    "judge_note",
    "machine_cut",
    "matched_note",
    "refusal",
    "resplit_halves",
    "resplit_note",
    "transform_note",
    "transform_oversized",
    "transform_resplit",
]

T = TypeVar("T")

MAX_BISECT_DEPTH = 3  # halvings per row after a provider 400 — 8x at most

_MACHINE_CUTS = frozenset({"tokens", "pages", "minutes", "seconds"})


def machine_cut(source: ItemSource) -> bool:
    """User cuts (file/lines/jsonl) are the user's boundaries — never re-split.
    Machine cuts came from an estimator, and estimators can be wrong."""
    return source.cut in _MACHINE_CUTS


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


def resplit_note(where: str) -> str:
    return f"{where} chunk re-split: provider rejected the estimate"


@dataclass(slots=True)
class RowNote:
    """Once-per-row disclosure latch for the re-split note."""

    where: str
    noted: bool = False

    def resplit(self) -> None:
        if not self.noted:
            self.noted = True
            diagnostics.note(resplit_note(self.where))


# --- bisect-on-context-400 (item 3) ------------------------------------------------


def _halves(text: str) -> tuple[str, ...]:
    """Split one machine-cut text in half (boundary-aware); () when it can't
    shrink any further."""
    estimate = estimate_tokens(text)
    if estimate < 2:
        return ()
    pieces = split_text(text, max(estimate // 2, 1))
    return pieces if len(pieces) > 1 else ()


def resplit_halves(text: str, *, cause: ItemError) -> tuple[str, ...]:
    """The halves for a worker-level re-split; re-raises the wire's own error
    when the text can't shrink (the 400 wasn't about size)."""
    halves = _halves(text)
    if not halves:
        raise cause
    return halves


async def _bisecting(
    call: Callable[[str], Awaitable[T]],
    text: str,
    *,
    note: RowNote,
    depth: int = MAX_BISECT_DEPTH,
) -> list[T]:
    """Run one chunk call; on a context-length 400 split the chunk in half and
    retry both halves (recursive, bounded). Non-overflow errors propagate."""
    try:
        return [await call(text)]
    except ItemError as exc:
        if depth <= 0 or not is_context_overflow(str(exc)):
            raise
        halves = _halves(text)
        if not halves:
            raise
        note.resplit()
        results: list[T] = []
        for half in halves:
            results.extend(await _bisecting(call, half, note=note, depth=depth - 1))
        return results


async def judge_bisected(
    judge: Callable[[str], Awaitable[bool]],
    text: str,
    *,
    note: RowNote,
    depth: int = MAX_BISECT_DEPTH,
) -> bool:
    """The judge flavor: OR the halves' verdicts, early exit on the first true.
    Public for join, whose auto-chunks are embed-budget-sized — bigger than a
    small local chat window can hold."""
    try:
        return await judge(text)
    except ItemError as exc:
        if depth <= 0 or not is_context_overflow(str(exc)):
            raise
        halves = _halves(text)
        if not halves:
            raise
        note.resplit()
        for half in halves:
            if await judge_bisected(judge, half, note=note, depth=depth - 1):
                return True
        return False


# --- map/extend: split → per-chunk call → one synthesis call ----------------------


async def transform_oversized(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    item: Item,
    over: Oversize,
    *,
    keep_invalid: bool = False,
    already_resplit: bool = False,
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
    note = RowNote(where, noted=already_resplit)  # once per row, resplit path included
    if structured:
        records: list[Mapping[str, object]] = []
        for position, chunk in enumerate(chunks):
            media = _chunk_media(item, position)

            async def extract(
                text: str, media: tuple[MediaData, ...] = media
            ) -> Mapping[str, object]:
                return await _extract_chunk(model, plan, instruction, text, media)

            records.extend(await _bisecting(extract, chunk, note=note))
        return await _merge(model, plan, instruction, records, keep_invalid=keep_invalid)
    partials: list[str] = []
    for position, chunk in enumerate(chunks):
        media = _chunk_media(item, position)

        async def answer(text: str, media: tuple[MediaData, ...] = media) -> str:
            return await _plain_chunk(model, plan, instruction, text, media)

        partials.extend(await _bisecting(answer, chunk, note=note))
    return await _combine(model, instruction, partials, over.budget)


async def transform_resplit(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    item: Item,
    *,
    keep_invalid: bool = False,
    cause: ItemError,
) -> str | Mapping[str, object]:
    """A MACHINE-cut item within the estimated budget that the wire rejected
    with a context-length 400 anyway: disclose, halve, and run the ordinary
    auto-chunk ladder on the halves."""
    estimate = estimate_tokens(item.text)
    if estimate < 2:  # can't halve a one-token text — the 400 wasn't about size
        raise cause
    from smartpipe.verbs.common import Oversize

    diagnostics.note(resplit_note(describe_source(item.source)))
    over = Oversize(estimate=estimate, budget=max(estimate // 2, 1))
    return await transform_oversized(
        model, plan, instruction, item, over, keep_invalid=keep_invalid, already_resplit=True
    )


def _chunk_media(item: Item, position: int) -> tuple[MediaData, ...]:
    return item.media if position == 0 else ()


async def _plain_chunk(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    chunk: str,
    media: tuple[MediaData, ...],
) -> str:
    request = build_map_request(plan, instruction, render_input(chunk), media=media)
    reply = await model.complete(request)
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
    from smartpipe.verbs.common import note_ambiguous_temporal

    request = build_map_request(plan, instruction, render_input(chunk), media=media)
    reply = await model.complete(request)
    try:
        return validate_and_coerce(reply, plan.schema, note=note_ambiguous_temporal)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        return validate_and_coerce(
            await model.complete(repair), plan.schema, note=note_ambiguous_temporal
        )


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
    from smartpipe.verbs.common import note_ambiguous_temporal

    request = build_merge_request(instruction, records, plan.schema)
    reply = await model.complete(request)
    try:
        return validate_and_coerce(reply, plan.schema, note=note_ambiguous_temporal)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        repaired = await model.complete(repair)
        try:
            return validate_and_coerce(repaired, plan.schema, note=note_ambiguous_temporal)
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
    plan is noted before the first call, the matching chunk after. A chunk the
    wire still rejects with a context 400 bisects (it is machine-cut by
    construction)."""
    diagnostics.note(judge_note(where, estimate, len(chunks)))
    note = RowNote(where)
    for position, chunk in enumerate(chunks, start=1):
        if await judge_bisected(judge, chunk, note=note):
            diagnostics.note(matched_note(where, position, len(chunks)))
            return True
    return False
