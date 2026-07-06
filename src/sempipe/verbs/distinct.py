"""The ``distinct`` verb: near-duplicate folding (D38/03, KQL ``distinct``).

Exact duplicates fold for free (hashing, before any embedding is spent); the
rest embed once and leader-cluster. First occurrence wins, input order and
bytes are preserved, and the receipt states exactly what was folded — the
training-data decontamination move (P12) and the alert-storm collapser (P6).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, UsageFault
from sempipe.engine.clustering import leader_clusters
from sempipe.engine.runner import Done, FailurePolicy
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.writers import RenderMode, WriterConfig, make_writer
from sempipe.verbs.common import embed_in_batches
from sempipe.verbs.convert import make_converter
from sempipe.verbs.embed import optional_chat

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.models.base import ChatModel, EmbeddingModel

__all__ = ["DistinctRequest", "run_distinct"]

_DEFAULT_THRESHOLD = 0.90


@dataclass(frozen=True, slots=True)
class DistinctRequest:
    show_groups: bool = False
    threshold: float = _DEFAULT_THRESHOLD
    model_flag: str | None = None
    concurrency_flag: int | None = None
    allow_captions: bool = False
    input: InputSpec = STDIN


class DistinctContext(Protocol):
    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...


async def run_distinct(
    request: DistinctRequest,
    context: DistinctContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    if not 0.0 < request.threshold <= 1.0:
        raise UsageFault("--threshold is a cosine similarity: between 0 and 1")
    model = await context.embedding_model(request.model_flag)
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
    items = [item async for item in items_iter]
    if not items:
        return ExitCode.OK

    # exact fast path: identical text folds for free, before any embedding
    first_of: dict[str, int] = {}
    exact_dupes_of: dict[int, list[int]] = {}
    uniques: list[Item] = []
    unique_positions: list[int] = []
    exact_folded = 0
    for position, item in enumerate(items):
        key = item.text.strip() if not item.media else f"\x00media:{position}"
        seen_at = first_of.get(key)
        if seen_at is not None:
            exact_dupes_of.setdefault(seen_at, []).append(position)
            exact_folded += 1
            continue
        first_of[key] = position
        uniques.append(item)
        unique_positions.append(position)

    log = diagnostics.DegradationLog()
    converter = make_converter(
        await optional_chat(context), allow_paid=request.allow_captions, log=log
    )
    vectors: dict[int, tuple[float, ...]] = {}  # original position → vector
    unexamined: list[int] = []  # embed-skipped: kept, disclosed
    outcomes = embed_in_batches(
        model,
        uniques,
        failure_policy=FailurePolicy(),
        stop=stop,
        log=log,
        converter=converter,
    )
    embed_order: list[int] = []  # positions, in outcome order
    async for outcome in outcomes:
        if isinstance(outcome, Done):
            embedded_item, vector = outcome.value
            del embedded_item
            position = unique_positions[len(embed_order) + len(unexamined)]
            vectors[position] = vector
            embed_order.append(position)
        else:  # Skipped: keep the item — never silently drop what we couldn't compare
            position = unique_positions[len(embed_order) + len(unexamined)]
            unexamined.append(position)
            diagnostics.warn(
                f"kept unexamined: {describe_source(outcome.source)} ({outcome.reason})"
            )
    log.finish()

    clusters = leader_clusters([vectors[p] for p in embed_order], threshold=request.threshold)
    kept: set[int] = set(unexamined)
    near_dupes_of: dict[int, list[int]] = {}
    near_folded = 0
    for members in clusters:
        leader_position = embed_order[members[0]]
        kept.add(leader_position)
        followers = [embed_order[m] for m in members[1:]]
        near_dupes_of[leader_position] = followers
        near_folded += len(followers)

    total = len(items)
    diagnostics.note(
        f"distinct: kept {len(kept):,} of {total:,} "
        f"({exact_folded:,} exact + {near_folded:,} near duplicates folded)"
    )

    if request.show_groups:
        writer = make_writer(
            WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=None), stdout
        )
        for position in sorted(kept):
            duplicates = [
                items[p].text
                for p in (*near_dupes_of.get(position, ()), *exact_dupes_of.get(position, ()))
            ]
            # exact dupes of folded near-dupes belong to the group too
            for follower in near_dupes_of.get(position, ()):
                duplicates.extend(items[p].text for p in exact_dupes_of.get(follower, ()))
            writer.write_record(
                {
                    "kept": items[position].text,
                    "count": 1 + len(duplicates),
                    "duplicates": duplicates,
                }
            )
        writer.flush()
        return ExitCode.OK

    for position in sorted(kept):
        stdout.write(items[position].raw + "\n")
    return ExitCode.OK
