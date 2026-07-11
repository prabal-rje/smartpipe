"""The ``top_k`` verb: rank items by similarity to a query (spec §3.4).

Embeds the query and every item (reusing a precomputed ``vector`` field from an
``embed`` record when present), ranks by cosine, and keeps the top K and/or
everything above a threshold — reordered, each with a ``__score``. Unlike the
per-item verbs, ``top_k`` inherently buffers: it must see all scores to rank.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from smartpipe.core.errors import (
    CircuitOpenTransport,
    ExcludedError,
    ExitCode,
    ItemError,
    LateSetupFault,
    RetryableError,
    SetupFault,
    SourceCounts,
    UnsentError,
    UsageFault,
)
from smartpipe.core.jsontools import as_float_vector
from smartpipe.engine.ranking import board_insert, cosine, rank, select, unit_score
from smartpipe.engine.runner import Done, FailurePolicy, run_ordered
from smartpipe.io import diagnostics, readers, source_accounting, tty
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source, project_content
from smartpipe.io.leaderboard import LiveBoard
from smartpipe.io.progress import make_stderr_spinner
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
from smartpipe.verbs.common import (
    ExecutionPolicySource,
    GeometryFence,
    embed_in_batches,
    ensure_text,
    interrupted_exit_code,
    media_embedder,
    native_route,
    note_native_once,
    outcome_exit_code,
)
from smartpipe.verbs.convert import Converter, make_converter

if TYPE_CHECKING:
    from typing import TextIO

    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.io.writers import ResultWriter
    from smartpipe.models.base import ChatModel, EmbeddingModel, ModelRef
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.stt import Transcriber

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
    fields: tuple[str, ...] | None = None  # --fields: project structured records
    allow_captions: bool = False  # cloud conversions opt-in (D33)
    media_model_flag: str | None = None  # --media-embed-model: the joint-space role
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion


class TopKContext(ExecutionPolicySource, Protocol):
    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> Transcriber | None: ...
    def document_parser(self, flag: str | None = None) -> DocumentParser | None: ...
    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    async def media_embedding_model(self, flag: str | None = None) -> EmbeddingModel | None: ...


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
    media_model = await context.media_embedding_model(request.media_model_flag)
    concurrency = context.concurrency(request.concurrency_flag)
    failure_policy = context.failure_policy(model.ref.provider)

    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    items_iter, _total = readers.resolve_items(request.input, stdin, ocr=ocr)
    items = [item async for item in items_iter]  # whole-set verbs need everything
    if not items:
        return outcome_exit_code(done=0, skipped=0, failed=0, input_count=0)

    effective = media_embedder(model, media_model)
    fence = GeometryFence(text_ref=str(model.ref), media_ref=str(effective.ref))
    any_native = False
    for item in items:
        if _precomputed_vector(item) is not None:
            continue  # already embedded — its __embedder stamp is the witness
        is_media = native_route(item, effective) is not None
        any_native = any_native or is_media
        fence.admit(media=is_media)  # fires before any spend
    # the query follows the corpus's space: media-native corpora rank text
    # queries in the JOINT space (that is the point of a media embedder)
    query_model = effective if (media_model is not None and any_native) else model
    query_sources = source_accounting.SourceCounter()
    for item in items:
        query_sources.skip(item.source, failed=False)
    query_vector = await _embed_query(
        query_model,
        request.near,
        context.failure_policy(query_model.ref.provider),
        late_counts=query_sources.counts,
    )
    stamps = _StampGate(query_ref=str(query_model.ref))
    converter_chat = await _optional_chat(context)
    converter = make_converter(
        converter_chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(converter_chat.ref if converter_chat else None),
        ocr=ocr,
    )
    vectors, skipped, failed, source_counts = await _collect_vectors(
        model,
        items,
        log,
        converter,
        failure_policy=failure_policy,
        call_concurrency=concurrency,
        media_model=media_model,
        stamps=stamps,
    )
    stamps.finish()
    log.finish()
    _check_dimensions(query_vector, vectors)

    entries = sorted(vectors.items())  # (ordinal, vector), stable by input order for ties
    ranked = rank(query_vector, [vector for _, vector in entries])
    scored = tuple((entries[position][0], score) for position, score in ranked)
    chosen = select(scored, k=request.k, threshold=request.threshold)

    # keyed by run-scoped ordinal, never source.index — two page-cut inputs
    # can share positions, and a collision emits the wrong item (item 47)
    by_ordinal = dict(enumerate(items))
    writer = make_writer(
        WriterConfig(mode=RenderMode.TEXT, color=False, width=80, fields=request.fields), stdout
    )
    for ordinal, score in chosen:
        _emit(writer, by_ordinal[ordinal], score)
    writer.flush()

    return outcome_exit_code(
        done=len(vectors),
        skipped=skipped,
        failed=failed,
        input_count=len(items),
        source_counts=source_counts,
    )


async def _run_stream(
    request: TopKRequest,
    context: TopKContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
) -> ExitCode:
    """The rolling leaderboard (stage-08 §4.3): maintain the top K as items arrive.

    Pipe mode emits an JSONL snapshot (a ``{"__snapshot": seq}`` marker line, then
    the K records, rank order) whenever membership/order changes; TTY mode repaints
    the block in place. A vector whose dimensions don't match the query is skipped
    (a stream shouldn't die wholesale on one bad record — unlike batch, where a
    mismatched corpus is a setup fault).
    """
    if request.k is None:
        raise UsageFault(
            "top_k --stream needs K (a live leaderboard has a size)\n"
            '  Example: tail -f tickets.jsonl | smartpipe top_k 5 --stream --near "billing dispute"'
        )
    if request.input.patterns or request.input.from_files:
        raise UsageFault(
            "top_k --stream reads a stream from stdin — it can't combine with --in\n"
            "  File inputs are a finite batch. Drop --stream, or pipe the stream in."
        )
    k = request.k
    model = await context.embedding_model(request.model_flag)
    media_model = await context.media_embedding_model(request.media_model_flag)
    effective = media_embedder(model, media_model)
    concurrency = context.concurrency(request.concurrency_flag)
    failure_policy = context.failure_policy(model.ref.provider)
    query_vector = await _embed_query(model, request.near, failure_policy)
    # a stream's composition is unknowable up front, so the query leads in the
    # TEXT space; a media item arriving under a split-role setup trips the fence
    fence = GeometryFence(text_ref=str(model.ref), media_ref=str(effective.ref), saw_text=True)
    stamps = _StampGate(query_ref=str(model.ref))
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    converter_chat = await _optional_chat(context)
    converter = make_converter(
        converter_chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(converter_chat.ref if converter_chat else None),
        ocr=ocr,
    )

    async def worker(item: Item) -> tuple[Item, tuple[float, ...]]:
        item = project_content(item)  # a record ranks by its content, not its wrapper
        native = native_route(item, effective)
        fence.admit(media=native is not None)
        if native is not None:
            narrowed, image = native
            note_native_once(effective)
            vector = (await narrowed.embed_parts([image]))[0]
            return item, _query_sized(vector, query_vector)
        item = await ensure_text(item, log=log, converter=converter)  # D33 ladder
        vector = _precomputed_vector(item)
        if vector is not None:
            stamps.check(item)
        else:
            vector = (await model.embed([item.text]))[0]
        return item, _query_sized(vector, query_vector)

    board: tuple[tuple[float, int], ...] = ()
    by_arrival: dict[int, Item] = {}
    live = _make_live_board(stdout)
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=request.fields), stdout
    )
    snapshot_seq = 0
    scored = 0
    skipped = 0
    failed = 0
    sources = source_accounting.SourceCounter()
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop, ocr=ocr)
    outcomes = run_ordered(
        items_iter,
        worker,
        concurrency=concurrency,
        failure_policy=failure_policy,
        stop=stop,
        halt_sources=sources,
    )
    try:
        async for outcome in outcomes:
            if not isinstance(outcome, Done):
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
                failed += int(outcome.failed)
                sources.skip(outcome.source, failed=outcome.failed)
                continue
            item, vector = outcome.value
            sources.done(item.source)
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
        stamps.finish()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=scored, skipped=skipped)
        return interrupted_exit_code(
            done=scored,
            skipped=skipped,
            failed=failed,
            source_counts=sources.counts,
        )
    return outcome_exit_code(
        done=scored,
        skipped=skipped,
        failed=failed,
        source_counts=sources.counts,
    )


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
    writer.write_record({"__snapshot": seq})
    for position, (score, arrival) in enumerate(board, start=1):
        item = by_arrival[arrival]
        record: dict[str, object]
        if item.data is not None:
            record = {key: value for key, value in item.data.items() if key != "vector"}
        else:
            record = {"text": item.raw}
        record["__score"] = round(score, 4)
        record["__rank"] = position
        writer.write_record(record)


async def _optional_chat(context: TopKContext) -> ChatModel | None:
    """The converter's LLM rung — absent when chat isn't configured (D33)."""
    try:
        return await context.chat_model()
    except Exception:
        return None


async def _embed_query(
    model: EmbeddingModel,
    text: str,
    failure_policy: FailurePolicy,
    *,
    late_counts: SourceCounts | None = None,
) -> tuple[float, ...]:
    """Map the required query call to setup semantics, never a BUG-70 item fault."""
    try:
        vectors = await model.embed([text])
        if len(vectors) != 1:
            raise ItemError(f"query embed returned {len(vectors)} vectors")
        return vectors[0]
    except CircuitOpenTransport as caught:
        cause = caught
        message = failure_policy.transport_screen or f"embedding provider unavailable ({caught})"
    except RetryableError as caught:
        cause = caught
        message = f"error: top_k query embedding failed after bounded retries ({caught})"
    except (ExcludedError, UnsentError) as caught:
        raise SetupFault(f"error: couldn't embed the top_k query ({caught})") from caught
    except ItemError as caught:
        cause = caught
        message = f"error: couldn't embed the top_k query ({caught})"
    settled = SourceCounts(0, 0, 0) if late_counts is None else late_counts
    raise LateSetupFault(message, source_counts=settled) from cause


@dataclass(slots=True)
class _StampGate:
    """The ``__embedder`` witness (item 40): a precomputed vector must come
    from the space this run's query lives in. Old unstamped rows keep working
    behind one calm note — they predate the stamp, not the geometry."""

    query_ref: str
    unstamped: int = 0

    def check(self, item: Item) -> None:
        stamp = item.data.get("__embedder") if item.data is not None else None
        if not isinstance(stamp, str):
            self.unstamped += 1
            return
        if stamp != self.query_ref:
            raise SetupFault(
                f"error: the corpus was embedded with {stamp}, "
                f"but this run embeds with {self.query_ref}\n"
                "  Vectors from two models live in different spaces — similarity across "
                "them is noise.\n"
                f"  Re-run with --embed-model {stamp}, or rebuild the corpus: "
                f"smartpipe embed --embed-model {self.query_ref}"
            )

    def finish(self) -> None:
        if not self.unstamped:
            return
        rows = "row" if self.unstamped == 1 else "rows"
        diagnostics.note(
            f"{self.unstamped} corpus {rows} carry no __embedder stamp (older embed "
            f"output) — assuming they match {self.query_ref}"
        )


def _query_sized(vector: tuple[float, ...], query: tuple[float, ...]) -> tuple[float, ...]:
    if len(vector) != len(query):
        raise ItemError(f"embedding dimensions {len(vector)} don't match the query ({len(query)})")
    return vector


async def _collect_vectors(
    model: EmbeddingModel,
    items: list[Item],
    log: diagnostics.DegradationLog,
    converter: Converter,
    *,
    failure_policy: FailurePolicy,
    call_concurrency: int,
    media_model: EmbeddingModel | None = None,
    stamps: _StampGate | None = None,
) -> tuple[dict[int, tuple[float, ...]], int, int, SourceCounts]:
    """Embed everything that needs embedding — chunked (≤64/call, DEFER-3),
    run_ordered bypassed on purpose: batching ≠ per-item workers (order comes
    from sequential chunks, isolation from the per-item poison fallback).

    The returned dict is keyed by run-scoped ordinal (enumerate order), never
    ``source.index`` — two page-cut inputs can share positions (item 47)."""
    vectors: dict[int, tuple[float, ...]] = {}
    to_embed: list[tuple[int, Item]] = []
    sources = source_accounting.SourceCounter()
    for ordinal, item in enumerate(items):
        precomputed = _precomputed_vector(item)
        if precomputed is not None:
            if stamps is not None:
                stamps.check(item)
            vectors[ordinal] = precomputed
            sources.done(item.source)
        else:
            to_embed.append((ordinal, project_content(item)))

    spinner = make_stderr_spinner()
    spinner.start(total=len(to_embed))
    skipped = 0
    failed = 0
    outcomes = embed_in_batches(
        model,
        [entry for _, entry in to_embed],
        failure_policy=failure_policy,
        call_concurrency=call_concurrency,
        log=log,
        converter=converter,
        media_model=media_model,
    )
    # one outcome per input, in input order (embed_in_batches' contract) — the
    # position maps each outcome back to its ordinal; outcome.index would be
    # the colliding source.index
    position = 0
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                embedded, vector = outcome.value
                vectors[to_embed[position][0]] = vector
                sources.done(embedded.source)
            else:  # Skipped
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
                failed += int(outcome.failed)
                sources.skip(outcome.source, failed=outcome.failed)
            position += 1
            spinner.advance()
    finally:
        spinner.finish()
        log.finish()
    return vectors, skipped, failed, sources.counts


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
                "  with the same --embed-model, or check SMARTPIPE_EMBED_MODEL."
            )


def _emit(writer: ResultWriter, item: Item, score: float) -> None:
    rounded = round(score, 4)
    # rank whole files → get filenames back (the resume demo); row/line cuts
    # from a file (--as jsonl/lines) are records and keep their fields
    if item.source.kind == "file" and item.source.cut == "file":
        writer.write_text(f"{item.source.name}\t{rounded}")
    elif item.data is not None:
        record = {key: value for key, value in item.data.items() if key != "vector"}
        record["__score"] = rounded
        writer.write_record(record)
    else:
        writer.write_text(f"{item.raw}\t{rounded}")
