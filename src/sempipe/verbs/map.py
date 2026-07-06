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
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, ItemError, UsageFault
from sempipe.engine.prompts import (
    build_map_request,
    build_repair_request,
    parse_prompt,
    plan_map,
    to_instruction,
)
from sempipe.engine.runner import Done, FailurePolicy, run_ordered
from sempipe.engine.schema import load_schema, validate_and_coerce
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.progress import make_stderr_spinner
from sempipe.models.base import AudioData
from sempipe.verbs.common import (
    WindowGate,
    interrupted_exit_code,
    outcome_exit_code,
    resolve_schema,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from sempipe.engine.prompts import MapPlan
    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import OutputFormat, ResultWriter
    from sempipe.models.base import ChatModel, ModelRef

__all__ = ["MapContext", "MapRequest", "run_map"]

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


class MapContext(Protocol):
    """The slice of the container ``map`` needs — a DI seam so tests inject fakes."""

    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    async def context_window(self, ref: ModelRef) -> int | None: ...
    def concurrency(self, flag: int | None = None) -> int: ...
    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
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
    model = await context.chat_model(request.model_flag)  # may emit a note / SetupFault
    structured = plan.mode == "structured"
    writer = context.writer(
        request.output, structured=structured, stdout=stdout, fields=request.fields
    )
    concurrency = context.concurrency(request.concurrency_flag)

    tally = None
    if request.tally_field is not None:
        if not structured:
            raise UsageFault(
                "--tally needs structured output — name fields in braces or pass --schema\n"
                '  Example: sempipe map "Extract {label}" --tally label'
            )
        from sempipe.engine.tally import Tally

        tally = Tally(request.tally_field)
    if request.explode_field is not None and not structured:
        raise UsageFault(
            "--explode needs structured output — name fields in braces or pass --schema\n"
            '  Example: sempipe map "Extract {risks}" --explode risks'
        )

    spinner = make_stderr_spinner()
    spinner.start(total=total)

    fallback_noted = [False]  # the once-per-run transcription note (ux.md pin)
    gate = WindowGate(
        provider=model.ref.provider,
        model_name=model.ref.name,
        overhead=_PROMPT_OVERHEAD_TOKENS,
        window=partial(context.context_window, model.ref),
    )

    async def worker(item: Item) -> str | Mapping[str, object]:
        budget = await gate.budget_for_oversized(item.text)
        if budget is not None:
            # D26: silently chunking would change what was asked — teach the pipeline
            raise ItemError(gate.refusal(item.text, budget))
        return await _map_one(model, plan, instruction, item, fallback_noted)

    done = 0
    skipped = 0
    outcomes = run_ordered(
        items_iter,
        worker,
        concurrency=concurrency,
        failure_policy=FailurePolicy(),
        stop=stop,
    )
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                for row in _rows(outcome.value, request.explode_field):
                    _write(writer, structured=structured, value=row)
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
    if tally is not None and tally.counts:
        diagnostics.note(tally.final_line())
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=done, skipped=skipped)
        return interrupted_exit_code(done=done, skipped=skipped)
    return outcome_exit_code(done=done, skipped=skipped)


async def _map_one(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    item: Item,
    fallback_noted: list[bool],
) -> str | Mapping[str, object]:
    media = (item.media,) if item.media is not None else ()
    request = build_map_request(plan, instruction, item.text, media=media)
    try:
        reply = await model.complete(request)
    except ItemError as native_failure:
        if not isinstance(item.media, AudioData):
            raise
        # the ladder's middle rung (D20 §5): the model can't hear it — transcribe
        # if the extra is there (MissingExtra keeps the two-fix skip), retry as text
        transcript = await asyncio.to_thread(_transcribe_or_skip, item.media, native_failure)
        if not fallback_noted[0]:
            fallback_noted[0] = True
            import os

            from sempipe.parsing.extract import whisper_size

            diagnostics.note(
                f"transcribing with local whisper ({whisper_size(os.environ)})"
                " — the model can't hear it natively"
            )
        spoken = replace(item, text=transcript, media=None)
        return await _map_one(model, plan, instruction, spoken, fallback_noted)
    if plan.schema is None:
        return reply.rstrip()  # plain mode: keep the reply, only trim trailing whitespace
    try:
        return validate_and_coerce(reply, plan.schema)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        repaired = await model.complete(repair)
        return validate_and_coerce(repaired, plan.schema)  # a second failure → Skipped


def _transcribe_or_skip(audio: AudioData, native_failure: ItemError) -> str:
    from sempipe.parsing.extract import MissingExtra, transcribe_audio

    try:
        return transcribe_audio(audio)
    except MissingExtra:
        raise native_failure from None  # the adapter's message already names both fixes


def _rows(
    value: str | Mapping[str, object], explode_field: str | None
) -> list[str | Mapping[str, object]]:
    if explode_field is None or not isinstance(value, Mapping):
        return [value]
    from sempipe.engine.tally import explode_record

    return list(explode_record(value, explode_field))


def _write(writer: ResultWriter, *, structured: bool, value: str | Mapping[str, object]) -> None:
    if structured and not isinstance(value, str):
        writer.write_record(value)
    else:
        writer.write_text(value if isinstance(value, str) else str(value))
