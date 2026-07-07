"""The ``diff`` verb: what distinguishes two sets of items (D38/06).

KQL's ``diffpatterns`` for meaning: embed both sides, cluster the union,
keep the lopsided themes. The post-incident "what changed" (P2), the eval
regression story (P7), and dataset drift before the GPU bill (P12).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode, UsageFault
from sempipe.engine.chunking import mean_pool
from sempipe.engine.clustering import adaptive_threshold, leader_clusters
from sempipe.engine.ranking import cosine
from sempipe.engine.runner import Done, FailurePolicy
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import ItemSource, describe_source, item_from_line
from sempipe.io.writers import RenderMode, WriterConfig, make_writer
from sempipe.verbs.cluster import ClusterContext, label_cluster
from sempipe.verbs.common import embed_in_batches
from sempipe.verbs.convert import make_converter
from sempipe.verbs.embed import optional_chat

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from sempipe.io.items import Item

__all__ = ["DiffRequest", "run_diff"]

_MIN_LOPSIDEDNESS = 0.05  # below this a theme is "both sides" — omitted by default
_EXAMPLES = 3


@dataclass(frozen=True, slots=True)
class DiffRequest:
    right: Path
    top: int | None = None
    show_all: bool = False
    model_flag: str | None = None  # chat, for theme labels
    embed_flag: str | None = None
    concurrency_flag: int | None = None
    allow_captions: bool = False


async def run_diff(
    request: DiffRequest,
    context: ClusterContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    model = await context.embedding_model(request.embed_flag)
    left_iter, _total = readers.resolve_items(STDIN, stdin, stop=stop)
    left = [item async for item in left_iter]
    right = _read_right(request.right)
    if not left or not right:
        raise UsageFault("diff needs items on BOTH sides — left is stdin, right is --right FILE")
    boundary = len(left)
    diagnostics.note(
        f"diff: left = stdin ({len(left):,}) · right = {request.right.name} "
        f"({len(right):,}) · ~{len(left) + len(right):,} embeddings + labels for "
        "the lopsided themes"
    )

    log = diagnostics.DegradationLog()
    converter_chat = await optional_chat(context)
    converter = make_converter(
        converter_chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(converter_chat.ref if converter_chat else None),
    )
    union_items: list[Item] = []
    vectors: list[tuple[float, ...]] = []
    outcomes = embed_in_batches(
        model,
        [*left, *right],
        failure_policy=FailurePolicy(),
        stop=stop,
        log=log,
        converter=converter,
    )
    position = 0
    kept_positions: list[int] = []
    async for outcome in outcomes:
        if isinstance(outcome, Done):
            embedded, vector = outcome.value
            union_items.append(embedded)
            vectors.append(vector)
            kept_positions.append(position)
        else:
            diagnostics.warn(f"excluded: {describe_source(outcome.source)} ({outcome.reason})")
        position += 1
    log.finish()
    if not vectors:
        return ExitCode.ALL_FAILED

    left_total = sum(1 for kept in kept_positions if kept < boundary)
    right_total = len(kept_positions) - left_total
    if left_total == 0 or right_total == 0:
        raise UsageFault("diff needs embeddable items on both sides")

    clusters = leader_clusters(vectors, threshold=adaptive_threshold(vectors))
    themes = [
        _theme(members, kept_positions, boundary, left_total, right_total) for members in clusters
    ]
    themes.sort(key=lambda theme: -theme.lopsidedness)

    chat = None
    lopsided = [theme for theme in themes if theme.side != "both"]
    shown = lopsided if request.top is None else lopsided[: request.top]
    if request.show_all:
        shown = [*shown, *(theme for theme in themes if theme.side == "both")]
    if shown:
        if request.model_flag is not None:
            chat = await context.chat_model(request.model_flag)
        else:
            chat = await optional_chat(context)

    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=None), stdout
    )
    for number, theme in enumerate(shown, start=1):
        if chat is not None:
            texts = [union_items[member].text for member in theme.dominant_members[:8]]
            label = await label_cluster(chat, texts, fallback=f"cluster {number}")
        else:
            label = f"cluster {number}"
        writer.write_record(
            {
                "side": theme.side,
                "theme": label,
                "share_left": round(theme.share_left, 2),
                "share_right": round(theme.share_right, 2),
                "examples": _examples(theme.dominant_members, union_items, vectors),
            }
        )
    writer.flush()
    omitted = len(themes) - len(lopsided)
    if omitted and not request.show_all:
        diagnostics.note(f"diff: {omitted} shared theme(s) omitted — --all shows them")
    if chat is None and shown:
        diagnostics.note("no chat model — themes are unnamed; configure one: smartpipe config")
    return ExitCode.OK


@dataclass(frozen=True, slots=True)
class _Theme:
    side: str  # "left" | "right" | "both"
    share_left: float
    share_right: float
    lopsidedness: float
    dominant_members: tuple[int, ...]  # indices into union_items, dominant side first


def _theme(
    members: list[int],
    kept_positions: list[int],
    boundary: int,
    left_total: int,
    right_total: int,
) -> _Theme:
    left_members = [m for m in members if kept_positions[m] < boundary]
    right_members = [m for m in members if kept_positions[m] >= boundary]
    share_left = len(left_members) / left_total
    share_right = len(right_members) / right_total
    lopsidedness = abs(share_left - share_right)
    dominant = left_members if share_left >= share_right else right_members
    minority = right_members if share_left >= share_right else left_members
    side = "both"
    if lopsidedness >= _MIN_LOPSIDEDNESS and len(dominant) >= 2:
        side = "left" if share_left > share_right else "right"
    return _Theme(
        side=side,
        share_left=share_left,
        share_right=share_right,
        lopsidedness=lopsidedness,
        dominant_members=(*dominant, *minority),
    )


def _examples(
    members: tuple[int, ...], items: list[Item], vectors: list[tuple[float, ...]]
) -> list[str]:
    pool = list(members[: _EXAMPLES * 4])
    centroid = mean_pool([vectors[member] for member in pool])
    nearest = sorted(pool, key=lambda member: -cosine(vectors[member], centroid))
    return [items[member].text[:160] for member in nearest[:_EXAMPLES]]


def _read_right(path: Path) -> list[Item]:
    if not path.exists():
        raise UsageFault(f"no such file: {path}\n  --right needs a JSONL or plain-lines file.")
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    return [
        replace(item_from_line(line, index), source=ItemSource("file", path.name, index))
        for index, line in enumerate(lines)
        if line.strip()
    ]
