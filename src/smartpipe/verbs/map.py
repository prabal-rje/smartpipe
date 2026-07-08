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

from smartpipe.core.errors import ExitCode, ItemError, UsageFault
from smartpipe.engine.prompts import (
    build_map_request,
    build_repair_request,
    parse_prompt,
    plan_map,
    to_instruction,
)
from smartpipe.engine.runner import Done, run_ordered
from smartpipe.engine.schema import load_schema, validate_and_coerce
from smartpipe.io import diagnostics, readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source, source_record
from smartpipe.io.progress import make_stderr_spinner
from smartpipe.models.base import AudioData, VideoData
from smartpipe.verbs.common import (
    ModelSlot,
    WindowGate,
    breaker_policy,
    interrupted_exit_code,
    make_failover,
    outcome_exit_code,
    resolve_schema,
)

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path
    from typing import TextIO

    from smartpipe.engine.prompts import MapPlan
    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.io.writers import OutputFormat, ResultWriter, TextSink
    from smartpipe.models.base import ChatModel, MediaData, ModelRef

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


class MapContext(Protocol):
    """The slice of the container ``map`` needs — a DI seam so tests inject fakes."""

    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    def fallback_ref(self, flag: str | None = None) -> ModelRef | None: ...
    async def fallback_chat_model(self, ref: ModelRef) -> ChatModel: ...
    async def context_window(self, ref: ModelRef) -> int | None: ...
    def concurrency(self, flag: int | None = None) -> int: ...
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
    instruction = to_instruction(tokens)
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    if request.dry_run:  # before model resolution: a dry run is free even pre-setup
        return await print_dry_run(plan, instruction, items_iter, stdout=stdout)
    model = await context.chat_model(request.model_flag)  # may emit a note / SetupFault
    slot = ModelSlot(model)
    fallback = context.fallback_ref(request.fallback_flag)  # embed refs refused here (free)
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

    tally = None
    if request.tally_field is not None:
        if not structured:
            raise UsageFault(
                "--tally needs structured output — name fields in braces or pass --schema\n"
                '  Example: smartpipe map "Extract {label}" --tally label'
            )
        from smartpipe.engine.tally import Tally

        tally = Tally(request.tally_field)
    if request.explode_field is not None and not structured:
        raise UsageFault(
            "--explode needs structured output — name fields in braces or pass --schema\n"
            '  Example: smartpipe map "Extract {risks}" --explode risks'
        )
    if request.keep_invalid and not structured:
        raise UsageFault(
            "--keep-invalid needs structured output — name fields in braces or pass --schema\n"
            "  Only schema validation can fail a row; plain text has nothing to validate."
        )

    spinner.start(total=total)

    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    gate = WindowGate(
        provider=model.ref.provider,
        model_name=model.ref.name,
        overhead=_PROMPT_OVERHEAD_TOKENS,
        window=partial(context.context_window, model.ref),
    )

    async def worker(item: Item) -> tuple[Item, str | Mapping[str, object]]:
        current = slot.current  # captured per item: the failover swaps wholesale
        budget = await gate.budget_for_oversized(item.text)
        if budget is not None:
            # D26: silently chunking would change what was asked — teach the pipeline
            raise ItemError(gate.refusal(item.text, budget))
        result = await map_one(
            current,
            plan,
            instruction,
            item,
            log,
            frame_every=request.frame_every,
            max_frames=request.max_frames,
            keep_invalid=request.keep_invalid,
        )
        slot.tally(str(current.ref))
        return item, result

    policy = breaker_policy(model.ref.provider)
    failover = (
        make_failover(
            slot, partial(context.fallback_chat_model, fallback), limit=policy.transport_limit
        )
        if fallback is not None
        else None
    )
    done = 0
    skipped = 0
    outcomes = run_ordered(
        items_iter,
        worker,
        concurrency=concurrency,
        failure_policy=policy,
        stop=stop,
        failover=failover,
    )
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                item, value = outcome.value
                if isinstance(value, str) and item.data is not None:
                    # records in, records out (item 14, law 4): a plain-prompt
                    # answer on a RECORD input is itself a record, spine attached
                    value = {"result": value, "__source": source_record(item.source)}
                for row in _rows(value, request.explode_field):
                    _write(writer, value=row)
                    if tally is not None and isinstance(row, Mapping):
                        tally.add(row)
                        spinner.extra = tally.live_segment()
                done += 1
            else:  # Skipped — the union has no third case
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
        log.finish()
    if tally is not None and tally.counts:
        diagnostics.note(tally.final_line())
    if slot.switched:
        diagnostics.note(slot.receipt())  # the seam stays visible (item 11)
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=done, skipped=skipped)
        return interrupted_exit_code(done=done, skipped=skipped)
    return outcome_exit_code(done=done, skipped=skipped)


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
            model, plan, instruction, item.text, item.media, keep_invalid=keep_invalid
        )
    except ItemError as native_failure:
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
            model, plan, instruction, spoken, remaining, keep_invalid=keep_invalid
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
        return await _attempt(model, plan, instruction, item.text, (video,))
    except ItemError:
        pass  # this wire can't watch — convert (the refusal cost nothing)
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
        return await _attempt(model, plan, instruction, item.text, media, keep_invalid=keep_invalid)
    except ItemError as native_failure:
        if track is None:
            raise
        # the model saw frames but couldn't hear — transcribe the track, retry
        transcript = await asyncio.to_thread(_transcribe_or_skip, track, native_failure)
        log.note(describe_source(item.source), "video audio → text", _whisper_note())
        spoken = f"{item.text}\n\n[audio track transcript]\n{transcript}".strip()
        return await _attempt(
            model, plan, instruction, spoken, parts.frames, keep_invalid=keep_invalid
        )


def _whisper_note() -> str:
    import os

    from smartpipe.parsing.extract import whisper_size

    return f"whisper {whisper_size(os.environ)}"


async def _attempt(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    text: str,
    media: tuple[MediaData, ...],
    *,
    keep_invalid: bool = False,
) -> str | Mapping[str, object]:
    request = build_map_request(plan, instruction, text, media=media)
    reply = await model.complete(request)
    if plan.schema is None:
        return reply.rstrip()  # plain mode: keep the reply, only trim trailing whitespace
    try:
        return validate_and_coerce(reply, plan.schema)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        repaired = await model.complete(repair)
        try:
            return validate_and_coerce(repaired, plan.schema)  # a second failure → Skipped
        except ItemError as second_error:
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
    text = first.text if first is not None else ""
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
    return ExitCode.OK


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
