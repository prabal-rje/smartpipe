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

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.engine.clustering import leader_clusters
from smartpipe.engine.runner import Done, FailurePolicy
from smartpipe.io import diagnostics, readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
from smartpipe.verbs.common import embed_in_batches
from smartpipe.verbs.convert import make_converter
from smartpipe.verbs.embed import optional_chat

if TYPE_CHECKING:
    from typing import TextIO

    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.models.base import ChatModel, EmbeddingModel, ModelRef
    from smartpipe.models.stt import RemoteTranscriber

__all__ = ["DistinctRequest", "run_distinct"]

_DEFAULT_THRESHOLD = 0.90


@dataclass(frozen=True, slots=True)
class DistinctRequest:
    show_groups: bool = False
    exact: bool = False  # --exact: stop at the hash rung — zero embedding calls (item 22)
    threshold: float = _DEFAULT_THRESHOLD
    model_flag: str | None = None
    concurrency_flag: int | None = None
    allow_captions: bool = False
    input: InputSpec = STDIN


class DistinctContext(Protocol):
    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> RemoteTranscriber | None: ...
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
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
    items = [item async for item in items_iter]
    if not items:
        return ExitCode.OK

    # exact fast path: identical items fold for free, before any embedding
    first_of: dict[str | bytes, int] = {}
    exact_dupes_of: dict[int, list[int]] = {}
    uniques: list[Item] = []
    unique_positions: list[int] = []
    exact_folded = 0
    for position, item in enumerate(items):
        key = _fold_key(item, position, exact=request.exact)
        seen_at = first_of.get(key)
        if seen_at is not None:
            exact_dupes_of.setdefault(seen_at, []).append(position)
            exact_folded += 1
            continue
        first_of[key] = position
        uniques.append(item)
        unique_positions.append(position)

    from smartpipe.io import manifest

    if request.exact:
        # --exact (item 22): the hash rung IS the answer — no embedding model
        # is even resolved, no fuzzy anything
        kept_exact = set(unique_positions)
        manifest.record_counts(done=len(items), skipped=0)  # every item was examined
        _receipt(kept=len(kept_exact), total=len(items), exact=exact_folded, near=0)
        return _emit(
            request,
            stdout,
            items,
            kept=kept_exact,
            near_dupes_of={},
            exact_dupes_of=exact_dupes_of,
        )

    model = await context.embedding_model(request.model_flag)
    log = diagnostics.DegradationLog()
    converter_chat = await optional_chat(context)
    converter = make_converter(
        converter_chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(converter_chat.ref if converter_chat else None),
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
    # unexamined rows are KEPT in the output but never compared - the honest skip count
    manifest.record_counts(done=len(items) - len(unexamined), skipped=len(unexamined))

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

    _receipt(kept=len(kept), total=len(items), exact=exact_folded, near=near_folded)
    return _emit(
        request,
        stdout,
        items,
        kept=kept,
        near_dupes_of=near_dupes_of,
        exact_dupes_of=exact_dupes_of,
    )


def _fold_key(item: Item, position: int, *, exact: bool) -> str | bytes:
    """--exact hashes honestly (item 22): records canonicalize to sorted-key
    compact JSON; media items hash their raw BYTES (identical files fold with
    zero model calls); plain text compares byte-for-byte — no fuzzy
    normalization, ever. The default rung keeps today's behavior: stripped
    text, media never exact-folds (the embedding rung handles it)."""
    if not exact:
        return item.text.strip() if not item.media else f"\x00media:{position}"
    if item.media:
        import hashlib

        digest = hashlib.sha256()
        for part in item.media:
            digest.update(part.data)
        return digest.digest()
    if item.data is not None:
        import json

        return "\x01rec:" + json.dumps(
            dict(item.data), sort_keys=True, ensure_ascii=False, separators=(",", ":")
        )
    return "\x02txt:" + item.raw


def _receipt(*, kept: int, total: int, exact: int, near: int) -> None:
    diagnostics.note(
        f"distinct: kept {kept:,} of {total:,} ({exact:,} exact + {near:,} near duplicates folded)"
    )


def _emit(
    request: DistinctRequest,
    stdout: TextIO,
    items: list[Item],
    *,
    kept: set[int],
    near_dupes_of: dict[int, list[int]],
    exact_dupes_of: dict[int, list[int]],
) -> ExitCode:
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
