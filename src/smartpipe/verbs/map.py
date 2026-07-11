"""The ``map`` verb: transform each item with a prompt (spec §3.1).

Orchestration only — the parsing, planning, schema work, and ordering all live in
the pure engine; this is the imperative shell that wires a resolved model to the
runner and streams results out. The one bit of per-verb cleverness is the single
repair retry: a structured reply that fails validation is re-asked once with the
validator's complaint before the item is skipped.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING, Protocol

from smartpipe.core.errors import (
    ExcludedError,
    ExitCode,
    ItemError,
    UsageFault,
    is_recoverable_item_error,
)
from smartpipe.engine.chunking import is_context_overflow
from smartpipe.engine.coalesce import max_group, worker_capacity
from smartpipe.engine.fieldpath import validate_field
from smartpipe.engine.prompts import (
    build_map_request,
    build_repair_request,
    parse_prompt,
    plan_map,
    render_input,
    to_instruction,
)
from smartpipe.engine.runner import Done, run_ordered
from smartpipe.engine.schema import load_schema, validate_and_coerce
from smartpipe.io import diagnostics, readers, source_accounting
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source, source_record
from smartpipe.io.progress import make_stderr_spinner
from smartpipe.models.base import AudioData, VideoData
from smartpipe.verbs.common import (
    ExecutionPolicySource,
    WindowGate,
    interrupted_exit_code,
    note_ambiguous_temporal,
    outcome_exit_code,
    resolve_schema,
    warn_unenforced_schema,
)
from smartpipe.verbs.oversize import machine_cut, transform_oversized, transform_resplit

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path
    from typing import TextIO

    from smartpipe.engine.coalesce import BatchSettings
    from smartpipe.engine.prompts import MapPlan
    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.io.writers import OutputFormat, ResultWriter, TextSink
    from smartpipe.models.base import ChatModel, MediaData, ModelRef
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.resilience import WiredChat

__all__ = ["MapContext", "MapRequest", "invalid_row", "map_one", "print_dry_run", "run_map"]

_PROMPT_OVERHEAD_TOKENS = 500  # instruction + wrapper + reply headroom


@dataclass(frozen=True, slots=True)
class MapRequest:
    prompt: str
    schema_path: Path | None
    model_flag: str | None
    output: OutputFormat
    concurrency_flag: int | None
    input: InputSpec = STDIN
    fields: tuple[str, ...] | None = None  # --fields: project structured output
    schema_dsl: str | None = None  # --schema-from (rung 3, D22)
    tally_field: str | None = None  # --tally FIELD: live distribution on stderr
    explode_field: str | None = None  # --explode FIELD: one row per list element
    frame_every: float | None = None  # --frame-every SECONDS: video density guarantee (D43)
    max_frames: int | None = None  # --max-frames N: video frame budget (D43)
    keep_invalid: bool = False  # --keep-invalid: failed validations become marker rows
    dry_run: bool = False  # --dry-run: print the composed first request, spend nothing
    fallback_flag: str | None = None  # --fallback-model: chat failover when the breaker trips
    bare: bool = False  # --bare: strip __ metadata from record output (item 18)
    full: bool = False  # --full: disable the TTY preview's truncation (item 19)
    whole: bool = False  # --whole: refuse oversized items instead of auto-chunking (D26 v2)
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion (item 40)


class MapContext(ExecutionPolicySource, Protocol):
    """The slice of the container ``map`` needs — a DI seam so tests inject fakes."""

    def document_parser(self, flag: str | None = None) -> DocumentParser | None: ...
    async def resilient_chat_model(
        self, flag: str | None = None, fallback_flag: str | None = None
    ) -> WiredChat: ...
    async def context_window(self, ref: ModelRef) -> int | None: ...
    def batching(self) -> BatchSettings | None: ...
    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextSink,
        fields: tuple[str, ...] | None = None,
        bare: bool = False,
        full: bool = False,
    ) -> ResultWriter: ...


async def run_map(
    request: MapRequest,
    context: MapContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    tokens = parse_prompt(request.prompt, allow_descriptions=True)  # rung 2 (D22)
    schema = resolve_schema(request.schema_path, request.schema_dsl, loader=load_schema)
    plan = plan_map(tokens, schema=schema)
    from smartpipe.io import manifest

    manifest.record_schema(plan.schema)  # the compiled schema, braces included (item 65a)
    instruction = to_instruction(tokens)
    if request.dry_run:  # before model resolution: a dry run is free even pre-setup
        items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
        return await print_dry_run(plan, instruction, items_iter, stdout=stdout)
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop, ocr=ocr)
    # The resilient stack: the primary wire + breaker + gate, with the configured
    # fallback armed underneath it (embed-ref fallbacks refused here, pre-spend).
    # `model` IS the resilient callable — the failover swaps to the backup inside
    # it, so the worker never branches on the wire's health (item 11).
    wired = await context.resilient_chat_model(request.model_flag, request.fallback_flag)
    model = wired.model  # may have emitted a note / SetupFault during resolution
    structured = plan.mode == "structured"
    spinner = make_stderr_spinner()
    # the arbiter: result writes pause the status line, so they never interleave
    writer = context.writer(
        request.output,
        structured=structured,
        stdout=spinner.guard(stdout),
        fields=request.fields,
        bare=request.bare,
        full=request.full,
    )
    concurrency = context.concurrency(request.concurrency_flag)
    batching = context.batching()  # item 62: eligible items coalesce into shared calls
    group_size = 1 if batching is None else max_group(plan.schema, batching.size)
    coalescing = group_size >= 2
    # Batching multiplexes many items onto few calls, so intake widens to fill a
    # group; `wire` keeps every SOLO path (media, oversized) at the documented
    # max-parallel-calls contract regardless of that boost.
    workers = worker_capacity(call_concurrency=concurrency, group_size=group_size)
    wire = asyncio.Semaphore(concurrency)

    tally = None
    if request.tally_field is not None:
        if not structured:
            raise UsageFault(
                "--tally needs structured output — name fields in braces or pass --schema\n"
                '  Example: smartpipe map "Extract {label}" --tally label'
            )
        from smartpipe.engine.tally import Tally

        # item 63: --tally takes a field path; grammar errors are loud pre-spend
        tally = Tally(validate_field(request.tally_field))
    if request.explode_field is not None:
        if not structured:
            raise UsageFault(
                "--explode needs structured output — name fields in braces or pass --schema\n"
                '  Example: smartpipe map "Extract {risks}" --explode risks'
            )
        validate_field(request.explode_field)  # item 63: paths, validated pre-spend
    if request.keep_invalid and not structured:
        raise UsageFault(
            "--keep-invalid needs structured output — name fields in braces or pass --schema\n"
            "  Only schema validation can fail a row; plain text has nothing to validate."
        )

    spinner.start(total=total)

    gate = WindowGate(
        provider=model.ref.provider,
        model_name=model.ref.name,
        overhead=_PROMPT_OVERHEAD_TOKENS,
        window=partial(context.context_window, model.ref),
    )

    async def worker(item: Item) -> tuple[Item, str | Mapping[str, object]]:
        # `model` is the resilient stack; the breaker routes to the fallback
        # underneath it, so the worker calls one plain model and never swaps.
        over = await gate.budget_for_oversized(
            item.text,
            item.media,
            provider=model.ref.provider,
            model_name=model.ref.name,
            window=partial(context.context_window, model.ref),
        )
        if over is not None and request.whole:
            # --whole: the old D26 refusal — reproducibility beats handling
            raise ExcludedError(gate.refusal(over))
        if over is not None:
            # D26 v2: handled, not skipped — chunk, transform, synthesize (disclosed).
            # Oversized items never batch (item 62 §7) — solo, wire-gated.
            async with wire:
                result: str | Mapping[str, object] = await transform_oversized(
                    model, plan, instruction, item, over, keep_invalid=request.keep_invalid
                )
        else:
            attempt = partial(
                map_one,
                model,
                plan,
                instruction,
                item,
                log,
                frame_every=request.frame_every,
                max_frames=request.max_frames,
                keep_invalid=request.keep_invalid,
            )
            try:
                if coalescing and not item.media:
                    result = await attempt(batch=True)  # coalescible — no wire gate:
                    # the shared flight is the call, and it is budgeted downstream
                else:
                    async with wire:  # media rides solo (item 62 §7), wire-gated
                        result = await attempt()
            except ItemError as exc:
                if (
                    request.whole
                    or not is_context_overflow(str(exc))
                    or not machine_cut(item.source)
                ):
                    raise
                # item 3: the wire rejected the estimate on a MACHINE-cut item
                # — halve and retry; user cuts stay per-item errors
                async with wire:
                    result = await transform_resplit(
                        model,
                        plan,
                        instruction,
                        item,
                        keep_invalid=request.keep_invalid,
                        cause=exc,
                    )
        wired.tally()  # count the answer under the model that answered it (item 11)
        return item, result

    policy = context.failure_policy(model.ref.provider)
    done = 0
    skipped = 0
    failed = 0
    sources = source_accounting.SourceCounter()
    outcomes = run_ordered(
        items_iter,
        worker,
        concurrency=workers,
        failure_policy=policy,
        stop=stop,
        fallback_armed=wired.armed,
        halt_sources=sources,
    )
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                item, value = outcome.value
                if isinstance(value, str) and item.data is not None:
                    # records in, records out (item 14, law 4): a plain-prompt
                    # answer on a RECORD input is itself a record, spine attached
                    value = {"result": value, "__source": source_record(item.source)}
                if isinstance(value, Mapping) and "__source" not in value:
                    # the __ spine rides every record (item 13): extraction drops
                    # the input's fields, so provenance is re-attached here — before
                    # --explode, so every exploded row carries it too
                    value = {**value, "__source": source_record(item.source)}
                for row in _rows(value, request.explode_field):
                    _write(writer, value=row)
                    if tally is not None and isinstance(row, Mapping):
                        tally.add(row)
                        spinner.extra = tally.live_segment()
                done += 1
                sources.done(item.source)
            else:  # Skipped — the union has no third case
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
                failed += int(outcome.failed)
                sources.skip(outcome.source, failed=outcome.failed)
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
        log.finish()
    if tally is not None and tally.counts:
        diagnostics.note(tally.final_line())
    if wired.switched:
        diagnostics.note(wired.receipt())  # the seam stays visible (item 11)
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


async def map_one(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    item: Item,
    log: diagnostics.DegradationLog,
    *,
    frame_every: float | None = None,
    max_frames: int | None = None,
    keep_invalid: bool = False,
    batch: bool = False,
) -> str | Mapping[str, object]:
    video = next((part for part in item.media if isinstance(part, VideoData)), None)
    if video is not None:
        return await _map_video(
            model,
            plan,
            instruction,
            item,
            video,
            log,
            frame_every=frame_every,
            max_frames=max_frames,
            keep_invalid=keep_invalid,
        )
    try:
        return await _attempt(
            model,
            plan,
            instruction,
            render_input(item),
            item.media,
            keep_invalid=keep_invalid,
            batch=batch,
        )
    except ItemError as native_failure:
        if not is_recoverable_item_error(native_failure):
            raise
        audio = next((part for part in item.media if isinstance(part, AudioData)), None)
        if audio is None:
            raise
        # the ladder's middle rung (D20 §5): the model can't hear it — transcribe
        # if the extra is there (MissingExtra keeps the two-fix skip), retry as text
        transcript = await asyncio.to_thread(_transcribe_or_skip, audio, native_failure)
        log.note(describe_source(item.source), "audio → text", _whisper_note())
        spoken = f"{item.text}\n\n{transcript}".strip() if item.text else transcript
        remaining = tuple(part for part in item.media if not isinstance(part, AudioData))
        return await _attempt(
            model, plan, instruction, render_input(spoken), remaining, keep_invalid=keep_invalid
        )


async def _map_video(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    item: Item,
    video: VideoData,
    log: diagnostics.DegradationLog,
    *,
    frame_every: float | None = None,
    max_frames: int | None = None,
    keep_invalid: bool = False,
) -> str | Mapping[str, object]:
    """Video ladder (D27/D34): the real thing where the wire watches it (gemini
    native accepts video; every other adapter refuses pre-send at zero cost),
    then frames + heard track, then frames + transcript."""
    try:
        return await _attempt(model, plan, instruction, render_input(item), (video,))
    except ItemError as watch_failure:
        if not is_recoverable_item_error(watch_failure):
            raise
    from functools import partial as _partial

    from smartpipe.parsing.extract import video_to_parts

    sample = _partial(
        video_to_parts,
        video,
        max_frames=max_frames if max_frames is not None else 24,
        every_seconds=frame_every,
    )
    parts = await asyncio.to_thread(sample)
    track = parts.track
    detail = f"{len(parts.frames)} frames" + (" + audio" if track is not None else ", silent")
    log.note(describe_source(item.source), "video → frames+audio", detail)
    media: tuple[MediaData, ...] = (*parts.frames, track) if track is not None else parts.frames
    try:
        return await _attempt(
            model, plan, instruction, render_input(item), media, keep_invalid=keep_invalid
        )
    except ItemError as native_failure:
        if not is_recoverable_item_error(native_failure):
            raise
        if track is None:
            raise
        # the model saw frames but couldn't hear — transcribe the track, retry
        transcript = await asyncio.to_thread(_transcribe_or_skip, track, native_failure)
        log.note(describe_source(item.source), "video audio → text", _whisper_note())
        spoken = f"{item.text}\n\n[audio track transcript]\n{transcript}".strip()
        return await _attempt(
            model, plan, instruction, render_input(spoken), parts.frames, keep_invalid=keep_invalid
        )


def _whisper_note() -> str:
    from smartpipe.parsing.extract import configured_whisper_size

    return f"whisper {configured_whisper_size()}"


async def _attempt(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    text: str,
    media: tuple[MediaData, ...],
    *,
    keep_invalid: bool = False,
    batch: bool = False,
) -> str | Mapping[str, object]:
    request = build_map_request(plan, instruction, text, media=media, batch=batch)
    reply = await model.complete(request)
    if plan.schema is None:
        return reply.rstrip()  # plain mode: keep the reply, only trim trailing whitespace
    try:
        return validate_and_coerce(reply, plan.schema, note=note_ambiguous_temporal)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        repaired = await model.complete(repair)
        try:
            # a second failure → Skipped
            return validate_and_coerce(repaired, plan.schema, note=note_ambiguous_temporal)
        except ItemError as second_error:
            # A3: the wire held a schema and still violated it twice — name the
            # cause once per run, so a run of skips reads as "wrong model", not
            # a mystery. Class-specific wording (loose wire vs strict) inside.
            warn_unenforced_schema(model)
            if not keep_invalid:
                raise
            # --keep-invalid: the failure becomes data — one valid row the user
            # can filter on (and rerun through another model) instead of a skip
            return invalid_row(error=str(second_error), raw=repaired)


def invalid_row(*, error: str, raw: str) -> dict[str, object]:
    """The --keep-invalid marker row: what failed, why, and the model's words."""
    return {"__invalid": True, "__error": error, "__raw": raw}


async def print_dry_run(
    plan: MapPlan,
    instruction: str,
    items_iter: AsyncIterator[Item],
    *,
    stdout: TextSink,
) -> ExitCode:
    """--dry-run: the fully composed first request on stdout (it IS the result),
    then exit 0 — no model call, no spend. Only the first item is consumed."""
    import json

    first = await anext(items_iter, None)
    if first is None:
        diagnostics.note("dry run: no input items — composing with an empty item")
    text = render_input(first) if first is not None else ""
    media = first.media if first is not None else ()
    composed = build_map_request(plan, instruction, text, media=media)
    sections = [f"--- system ---\n{composed.system}"]
    if plan.schema is not None:
        pretty = json.dumps(dict(plan.schema), indent=2, ensure_ascii=False)
        sections.append(f"--- schema ---\n{pretty}")
    if composed.media:
        kinds = " · ".join(_media_kind(part) for part in composed.media)
        sections.append(f"--- media ---\n{kinds}")
    sections.append(f"--- user ---\n{composed.user}")
    stdout.write("\n".join(sections) + "\n")
    stdout.flush()
    consumed = int(first is not None)
    return outcome_exit_code(
        done=consumed,
        skipped=0,
        failed=0,
        input_count=consumed,
    )


def _media_kind(part: MediaData) -> str:
    return type(part).__name__.removesuffix("Data").lower()


def _transcribe_or_skip(audio: AudioData, native_failure: ItemError) -> str:
    from smartpipe.parsing.extract import MissingExtra, transcribe_audio

    try:
        return transcribe_audio(audio)
    except MissingExtra:
        raise native_failure from None  # the adapter's message already names both fixes


def _rows(
    value: str | Mapping[str, object], explode_field: str | None
) -> list[str | Mapping[str, object]]:
    if explode_field is None or not isinstance(value, Mapping):
        return [value]
    from smartpipe.engine.tally import explode_record

    return list(explode_record(value, explode_field))


def _write(writer: ResultWriter, *, value: str | Mapping[str, object]) -> None:
    if isinstance(value, str):
        writer.write_text(value)  # the human door: plain text leaves as plain text (law 5)
    else:
        writer.write_record(value)
