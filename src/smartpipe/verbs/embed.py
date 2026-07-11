"""The ``embed`` verb: turn each item into a vector (spec §3.3).

The only verb that never touches a chat LLM — it uses the embedding model and
emits one JSONL record per item: ``{"text", "vector", "__embedder",
"__source"}`` (item 40: the honest spine — the stamp names the space the
vector lives in, and ``top_k`` refuses a corpus from another one). Output is
always JSONL (a vector has no human view), so it feeds ``top_k`` or a file.

Two execution shapes (plan/post-1.0/06, DEFER-3): a finite file corpus is
embedded in ≤64-text chunks (64x fewer round-trips, poison chunks re-run
item-by-item); a stream stays one item per call — latency beats throughput
when lines arrive over time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from smartpipe.core.errors import ExitCode
from smartpipe.engine.runner import Done, run_ordered
from smartpipe.io import diagnostics, readers, source_accounting, tty
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source, project_content, source_record
from smartpipe.io.progress import make_stderr_spinner
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
from smartpipe.models.base import VideoData
from smartpipe.verbs.common import (
    ExecutionPolicySource,
    GeometryFence,
    embed_in_batches,
    ensure_text,
    interrupted_exit_code,
    media_embedder,
    native_route,
    outcome_exit_code,
    row_embedder,
)
from smartpipe.verbs.convert import Converter, embed_video_halves, make_converter

if TYPE_CHECKING:
    from typing import TextIO

    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.models.base import ChatModel, EmbeddingModel, ModelRef
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.stt import Transcriber

__all__ = ["ChatSource", "EmbedContext", "EmbedRequest", "optional_chat", "run_embed"]


class ChatSource(Protocol):
    """The one method ``optional_chat`` needs — narrower than any verb context,
    so every embedding verb's context satisfies it structurally."""

    async def chat_model(self, flag: str | None = None) -> ChatModel: ...


@dataclass(frozen=True, slots=True)
class EmbedRequest:
    model_flag: str | None
    concurrency_flag: int | None
    allow_captions: bool = False  # cloud conversions opt-in (D33)
    input: InputSpec = STDIN
    fields: tuple[str, ...] | None = None  # --fields: project the output records
    media_model_flag: str | None = None  # --media-embed-model: the joint-space role
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion


class EmbedContext(ExecutionPolicySource, Protocol):
    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> Transcriber | None: ...
    def document_parser(self, flag: str | None = None) -> DocumentParser | None: ...
    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    async def media_embedding_model(self, flag: str | None = None) -> EmbeddingModel | None: ...


async def run_embed(
    request: EmbedRequest,
    context: EmbedContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    model = await context.embedding_model(request.model_flag)
    media_model = await context.media_embedding_model(request.media_model_flag)
    fence = _fence(model, media_model)
    concurrency = context.concurrency(request.concurrency_flag)
    failure_policy = context.failure_policy(model.ref.provider)

    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop, ocr=ocr)
    if (total is None or total > 0) and tty.stdout_is_tty():
        diagnostics.note(
            "embeddings are large — redirect to a file: smartpipe embed > corpus.embeddings"
        )
    spinner = make_stderr_spinner()
    # the arbiter: result writes pause the status line, so they never interleave
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=request.fields),
        spinner.guard(stdout),
    )
    spinner.start(total=total)
    converter_chat = await optional_chat(context)
    converter = make_converter(
        converter_chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(converter_chat.ref if converter_chat else None),
        ocr=ocr,
    )

    done = 0
    skipped = 0
    failed = 0
    sources = source_accounting.SourceCounter()
    # item 49(b): an OCR run reports total=None (page counts are unknown
    # pre-parse) but a files-only corpus is still FINITE — parse first
    # (pass one), then batch the embeds (pass two) instead of per-item calls
    finite = total is not None or readers.ocr_finite_paths(request.input, stdin)
    if finite:
        # finite --in corpus: chunked calls, run_ordered bypassed on purpose —
        # batching ≠ per-item workers (order from sequential chunks, isolation
        # from the per-item fallback inside embed_in_batches)
        collected = [project_content(item) async for item in items_iter]
        for entry in collected:  # the geometry fence fires BEFORE any spend here
            fence.admit(media=native_route(entry, media_embedder(model, media_model)) is not None)
        outcomes = embed_in_batches(
            model,
            collected,
            failure_policy=failure_policy,
            call_concurrency=concurrency,
            stop=stop,
            log=log,
            converter=converter,
            media_model=media_model,
        )
    else:
        # live stream: one item per call — latency beats throughput

        async def worker(item: Item) -> tuple[Item, tuple[float, ...]]:
            return await _embed_one(model, item, log, converter, media_model, fence)

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
            if isinstance(outcome, Done):
                item, vector = outcome.value
                writer.write_record(
                    {
                        "text": item.text,
                        "vector": list(vector),
                        "__embedder": row_embedder(item, model, media_model),
                        "__source": source_record(item.source),
                    }
                )
                done += 1
                sources.done(item.source)
            else:  # Skipped
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
                failed += int(outcome.failed)
                sources.skip(outcome.source, failed=outcome.failed)
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
        log.finish()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=done, skipped=skipped)
        return interrupted_exit_code(
            done=done,
            skipped=skipped,
            failed=failed,
            source_counts=sources.counts,
        )
    return outcome_exit_code(
        done=done,
        skipped=skipped,
        failed=failed,
        source_counts=sources.counts,
    )


async def optional_chat(context: ChatSource) -> ChatModel | None:
    """The converter's LLM rung — absent when no chat model is configured;
    embedding never fails because chat isn't set up (D33)."""
    try:
        return await context.chat_model()
    except Exception:
        return None


def _fence(model: EmbeddingModel, media_model: EmbeddingModel | None) -> GeometryFence:
    """The run's one-vector-space gate: equal refs can never trip it."""
    return GeometryFence(
        text_ref=str(model.ref), media_ref=str(media_embedder(model, media_model).ref)
    )


async def _embed_one(
    model: EmbeddingModel,
    item: Item,
    log: diagnostics.DegradationLog,
    converter: Converter,
    media_model: EmbeddingModel | None = None,
    fence: GeometryFence | None = None,
) -> tuple[Item, tuple[float, ...]]:
    # D39/04: a media-native embedder takes image items as PIXELS on the
    # streaming path too — the first live jina call caption-pivoted because
    # only the finite-corpus branch checked this route (live-caught)
    from smartpipe.verbs.common import note_native_once

    item = project_content(item)  # a record embeds its content, never its wrapper
    effective = media_embedder(model, media_model)
    native = native_route(item, effective)
    if fence is not None:
        fence.admit(media=native is not None)
    if native is not None:
        narrowed, image = native
        note_native_once(effective)
        vectors = await narrowed.embed_parts([image])
        return item, vectors[0]
    video = next((part for part in item.media if isinstance(part, VideoData)), None)
    if video is not None and converter.chat is not None:
        return await embed_video_halves(model, item, video, converter)  # 50/50 (D36)
    item = await ensure_text(item, log=log, converter=converter)  # D33 ladder
    vectors = await model.embed([item.text])
    return item, vectors[0]  # the CONVERTED item — its text is what the vector means
