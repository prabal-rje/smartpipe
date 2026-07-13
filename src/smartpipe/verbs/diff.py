"""The ``diff`` verb: what distinguishes two sets of items (D38/06).

KQL's ``diffpatterns`` for meaning: embed both sides, cluster the union,
keep the lopsided themes. The post-incident "what changed" (P2), the eval
regression story (P7), and dataset drift before the GPU bill (P12).
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode, SourceCounts, UsageFault
from smartpipe.engine.chunking import mean_pool
from smartpipe.engine.clustering import adaptive_threshold, leader_clusters
from smartpipe.engine.ranking import cosine
from smartpipe.engine.runner import Done
from smartpipe.io import diagnostics, readers, source_accounting
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
from smartpipe.verbs.cluster import ClusterContext, label_cluster
from smartpipe.verbs.common import embed_in_batches, outcome_exit_code
from smartpipe.verbs.convert import make_converter
from smartpipe.verbs.embed import optional_chat

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from smartpipe.io.items import Item
    from smartpipe.io.readers import OcrIngest
    from smartpipe.models.budget import CallBudget

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
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion (item 48)


async def run_diff(
    request: DiffRequest,
    context: ClusterContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
    budget: CallBudget | None = None,
) -> ExitCode:
    if request.top is not None and request.top < 1:
        raise UsageFault(f"--top must be >= 1, got {request.top}")
    concurrency = context.concurrency(request.concurrency_flag)
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    right = await _right_items(request.right, ocr)
    if not right:
        raise UsageFault("diff needs items on BOTH sides — left is stdin, right is --right FILE")
    left_iter, _total = readers.resolve_items(STDIN, stdin, stop=stop, ocr=ocr, budget=budget)
    left = [item async for item in left_iter]
    if not left or not right:
        raise UsageFault("diff needs items on BOTH sides — left is stdin, right is --right FILE")
    model = await context.embedding_model(request.embed_flag)
    failure_policy = context.failure_policy(model.ref.provider)
    boundary = len(left)
    diagnostics.note(
        f"diff: left = stdin ({len(left):,}) · right = {request.right.name} "
        f"({len(right):,}) · ~{len(left) + len(right):,} embeddings + labels for "
        "the lopsided themes"
    )

    converter_chat = await optional_chat(context)
    converter = make_converter(
        converter_chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(converter_chat.ref if converter_chat else None),
        ocr=ocr,
    )
    union_items: list[Item] = []
    vectors: list[tuple[float, ...]] = []
    failed = 0
    sources = source_accounting.SourceCounter()
    outcomes = embed_in_batches(
        model,
        [*left, *right],
        failure_policy=failure_policy,
        call_concurrency=concurrency,
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
            sources.done(embedded.source)
        else:
            diagnostics.warn(f"excluded: {describe_source(outcome.source)} ({outcome.reason})")
            failed += int(outcome.failed)
            sources.skip(outcome.source, failed=outcome.failed)
        position += 1
    log.finish()
    total_in = len(left) + len(right)
    excluded = total_in - len(union_items)
    if not vectors:
        return outcome_exit_code(
            done=0,
            skipped=total_in,
            failed=failed,
            input_count=total_in,
            source_counts=sources.counts,
        )

    left_total = sum(1 for kept in kept_positions if kept < boundary)
    right_total = len(kept_positions) - left_total
    if left_total == 0 or right_total == 0:
        counts = sources.counts
        return outcome_exit_code(
            done=0,
            skipped=total_in,
            failed=failed,
            input_count=total_in,
            source_counts=SourceCounts(
                succeeded=0,
                skipped=counts.total,
                failed=counts.failed,
            ),
        )

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
                # item 64: a synthesized theme row carries a summary spine
                "__source": {
                    "as": "diff",
                    "side": theme.side,
                    "count": len(theme.dominant_members),
                },
            }
        )
    writer.flush()
    omitted = len(themes) - len(lopsided)
    if omitted and not request.show_all:
        diagnostics.note(f"diff: {omitted} shared theme(s) omitted — --all shows them")
    if chat is None and shown:
        diagnostics.note("no chat model — themes are unnamed; configure one: smartpipe config")
    return outcome_exit_code(
        done=len(union_items),
        skipped=excluded,
        failed=failed,
        input_count=total_in,
        source_counts=sources.counts,
    )


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


async def _right_items(path: Path, ocr: OcrIngest | None) -> list[Item]:
    """The right side under the ocr-model role (item 48): a parseable
    PDF/image parses to page items; everything else — and an unset role —
    reads exactly as before (JSONL or plain lines)."""
    return await readers.read_right_items(path, ocr)
