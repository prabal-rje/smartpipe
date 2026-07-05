"""The ``top_k`` verb: rank items by similarity to a query (spec §3.4).

Embeds the query and every item (reusing a precomputed ``vector`` field from an
``embed`` record when present), ranks by cosine, and keeps the top K and/or
everything above a threshold — reordered, each with a ``_score``. Unlike the
per-item verbs, ``top_k`` inherently buffers: it must see all scores to rank.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, ItemError, SetupFault, UsageFault
from sempipe.core.jsontools import as_float_vector
from sempipe.engine.ranking import board_insert, cosine, rank, select, unit_score
from sempipe.engine.runner import Done, FailurePolicy, run_ordered
from sempipe.io import diagnostics, readers, tty
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.leaderboard import LiveBoard
from sempipe.io.progress import make_stderr_spinner
from sempipe.io.writers import RenderMode, WriterConfig, make_writer
from sempipe.verbs.common import (
    aiter_items,
    ensure_text_item,
    interrupted_exit_code,
    outcome_exit_code,
)

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import ResultWriter
    from sempipe.models.base import EmbeddingModel

__all__ = ["TopKContext", "TopKRequest", "run_top_k"]


@dataclass(frozen=True, slots=True)
class TopKRequest:
    near: str
    k: int | None
    threshold: float | None
    model_flag: str | None
    concurrency_flag: int | None
    input: InputSpec = STDIN
    stream: bool = False  # --stream: the live leaderboard (a different output protocol)


class TopKContext(Protocol):
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...


async def run_top_k(
    request: TopKRequest,
    context: TopKContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    if request.stream:
        return await _run_stream(request, context, stdin=stdin, stdout=stdout, stop=stop)
    if request.k is None and request.threshold is None:
        raise UsageFault("top_k needs a number (K), --threshold, or both")
    model = await context.embedding_model(request.model_flag)
    concurrency = context.concurrency(request.concurrency_flag)

    items_iter, _total = readers.resolve_items(request.input, stdin)
    items = [item async for item in items_iter]  # whole-set verbs need everything
    if not items:
        return ExitCode.OK

    query_vector = (await model.embed([request.near]))[0]
    vectors, skipped = await _collect_vectors(model, items, concurrency)
    _check_dimensions(query_vector, vectors)

    entries = sorted(vectors.items())  # (item_index, vector), stable by index for ties
    ranked = rank(query_vector, [vector for _, vector in entries])
    scored = tuple((entries[position][0], score) for position, score in ranked)
    chosen = select(scored, k=request.k, threshold=request.threshold)

    by_index = {item.source.index: item for item in items}
    writer = make_writer(WriterConfig(mode=RenderMode.TEXT, color=False, width=80), stdout)
    for item_index, score in chosen:
        _emit(writer, by_index[item_index], score)
    writer.flush()

    if skipped == 0:
        return ExitCode.OK
    return ExitCode.ALL_FAILED if not vectors else ExitCode.PARTIAL


async def _run_stream(
    request: TopKRequest,
    context: TopKContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
) -> ExitCode:
    """The rolling leaderboard (stage-08 §4.3): maintain the top K as items arrive.

    Pipe mode emits an NDJSON snapshot (a ``{"_snapshot": seq}`` marker line, then
    the K records, rank order) whenever membership/order changes; TTY mode repaints
    the block in place. A vector whose dimensions don't match the query is skipped
    (a stream shouldn't die wholesale on one bad record — unlike batch, where a
    mismatched corpus is a setup fault).
    """
    if request.k is None:
        raise UsageFault(
            "top_k --stream needs K (a live leaderboard has a size)\n"
            '  Example: tail -f tickets.jsonl | sempipe top_k 5 --stream --near "billing dispute"'
        )
    if request.input.patterns or request.input.from_files:
        raise UsageFault(
            "top_k --stream reads a stream from stdin — it can't combine with --in\n"
            "  File inputs are a finite batch. Drop --stream, or pipe the stream in."
        )
    k = request.k
    model = await context.embedding_model(request.model_flag)
    concurrency = context.concurrency(request.concurrency_flag)
    query_vector = (await model.embed([request.near]))[0]

    async def worker(item: Item) -> tuple[Item, tuple[float, ...]]:
        ensure_text_item(item)  # image items need map — ItemError → skip-and-warn
        vector = _precomputed_vector(item)
        if vector is None:
            vector = (await model.embed([item.text]))[0]
        if len(vector) != len(query_vector):
            raise ItemError(
                f"embedding dimensions {len(vector)} don't match the query ({len(query_vector)})"
            )
        return item, vector

    board: tuple[tuple[float, int], ...] = ()
    by_arrival: dict[int, Item] = {}
    live = _make_live_board(stdout)
    writer = make_writer(WriterConfig(mode=RenderMode.NDJSON, color=False, width=80), stdout)
    snapshot_seq = 0
    scored = 0
    skipped = 0
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
    outcomes = run_ordered(
        items_iter, worker, concurrency=concurrency, failure_policy=FailurePolicy(), stop=stop
    )
    try:
        async for outcome in outcomes:
            if not isinstance(outcome, Done):
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
                continue
            item, vector = outcome.value
            scored += 1
            score = unit_score(cosine(query_vector, vector))
            if request.threshold is not None and score < request.threshold:
                continue
            arrival = scored
            by_arrival[arrival] = item
            board, changed = board_insert(board, score, arrival, k)
            if not changed:
                continue
            if live is not None:
                live.paint(_board_rows(board, by_arrival))
            else:
                snapshot_seq += 1
                _emit_snapshot(writer, snapshot_seq, board, by_arrival)
    finally:
        if live is not None:
            live.paint(_board_rows(board, by_arrival), force=True)  # final state stays visible
        writer.flush()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=scored, skipped=skipped)
        return interrupted_exit_code(done=scored, skipped=skipped)
    return outcome_exit_code(done=scored, skipped=skipped)


def _make_live_board(stdout: TextIO) -> LiveBoard | None:
    if not tty.stdout_is_tty():
        return None
    import time

    return LiveBoard(stream=stdout, width=tty.terminal_width(), clock=time.monotonic)


def _board_rows(
    board: tuple[tuple[float, int], ...], by_arrival: dict[int, Item]
) -> list[tuple[float, str]]:
    return [(score, by_arrival[arrival].raw) for score, arrival in board]


def _emit_snapshot(
    writer: ResultWriter,
    seq: int,
    board: tuple[tuple[float, int], ...],
    by_arrival: dict[int, Item],
) -> None:
    writer.write_record({"_snapshot": seq})
    for position, (score, arrival) in enumerate(board, start=1):
        item = by_arrival[arrival]
        record: dict[str, object]
        if item.data is not None:
            record = {key: value for key, value in item.data.items() if key != "vector"}
        else:
            record = {"text": item.raw}
        record["_score"] = round(score, 4)
        record["_rank"] = position
        writer.write_record(record)


async def _collect_vectors(
    model: EmbeddingModel, items: list[Item], concurrency: int
) -> tuple[dict[int, tuple[float, ...]], int]:
    vectors: dict[int, tuple[float, ...]] = {}
    to_embed: list[Item] = []
    for item in items:
        precomputed = _precomputed_vector(item)
        if precomputed is not None:
            vectors[item.source.index] = precomputed
        else:
            to_embed.append(item)

    spinner = make_stderr_spinner()
    spinner.start(total=len(to_embed))
    skipped = 0

    async def worker(item: Item) -> tuple[float, ...]:
        ensure_text_item(item)  # image items need map — ItemError → skip-and-warn
        return (await model.embed([item.text]))[0]

    outcomes = run_ordered(
        aiter_items(to_embed), worker, concurrency=concurrency, failure_policy=FailurePolicy()
    )
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                vectors[outcome.index] = outcome.value
            else:  # Skipped
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
            spinner.advance()
    finally:
        spinner.finish()
    return vectors, skipped


def _precomputed_vector(item: Item) -> tuple[float, ...] | None:
    """An ``embed`` record carries its own ``vector`` — skip re-embedding it (spec §3.4)."""
    if item.data is None:
        return None
    return as_float_vector(item.data.get("vector"))


def _check_dimensions(query: tuple[float, ...], vectors: dict[int, tuple[float, ...]]) -> None:
    for vector in vectors.values():
        if len(vector) != len(query):
            raise SetupFault(
                f"error: the corpus and the query were embedded with different models "
                f"(dimensions {len(vector)} vs {len(query)})\n"
                "  Use the same embedding model for both — e.g. re-run embed and top_k\n"
                "  with the same --embed-model, or check SEMPIPE_EMBED_MODEL."
            )


def _emit(writer: ResultWriter, item: Item, score: float) -> None:
    rounded = round(score, 4)
    if item.source.kind == "file":  # rank files → get filenames back (the resume demo)
        writer.write_text(f"{item.source.name}\t{rounded}")
    elif item.data is not None:
        record = {key: value for key, value in item.data.items() if key != "vector"}
        record["_score"] = rounded
        writer.write_record(record)
    else:
        writer.write_text(f"{item.raw}\t{rounded}")
