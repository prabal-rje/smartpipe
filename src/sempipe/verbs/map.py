"""The ``map`` verb: transform each item with a prompt (spec §3.1).

Orchestration only — the parsing, planning, schema work, and ordering all live in
the pure engine; this is the imperative shell that wires a resolved model to the
runner and streams results out. The one bit of per-verb cleverness is the single
repair retry: a structured reply that fails validation is re-asked once with the
validator's complaint before the item is skipped.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, ItemError
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
from sempipe.verbs.common import interrupted_exit_code, outcome_exit_code

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path
    from typing import TextIO

    from sempipe.engine.prompts import MapPlan
    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import OutputFormat, ResultWriter
    from sempipe.models.base import ChatModel

__all__ = ["MapContext", "MapRequest", "run_map"]


@dataclass(frozen=True, slots=True)
class MapRequest:
    prompt: str
    schema_path: Path | None
    model_flag: str | None
    output: OutputFormat
    concurrency_flag: int | None
    input: InputSpec = STDIN


class MapContext(Protocol):
    """The slice of the container ``map`` needs — a DI seam so tests inject fakes."""

    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...
    def writer(
        self, output_flag: OutputFormat, *, structured: bool, stdout: TextIO
    ) -> ResultWriter: ...


async def run_map(
    request: MapRequest,
    context: MapContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    tokens = parse_prompt(request.prompt)  # UsageFault on bad grammar
    schema = load_schema(request.schema_path) if request.schema_path is not None else None
    plan = plan_map(tokens, schema=schema)
    instruction = to_instruction(tokens)
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    model = await context.chat_model(request.model_flag)  # may emit a note / SetupFault
    structured = plan.mode == "structured"
    writer = context.writer(request.output, structured=structured, stdout=stdout)
    concurrency = context.concurrency(request.concurrency_flag)

    spinner = make_stderr_spinner()
    spinner.start(total=total)

    async def worker(item: Item) -> str | Mapping[str, object]:
        return await _map_one(model, plan, instruction, item)

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
                _write(writer, structured=structured, value=outcome.value)
                done += 1
            else:  # Skipped — the union has no third case
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=done, skipped=skipped)
        return interrupted_exit_code(done=done, skipped=skipped)
    return outcome_exit_code(done=done, skipped=skipped)


async def _map_one(
    model: ChatModel, plan: MapPlan, instruction: str, item: Item
) -> str | Mapping[str, object]:
    images = (item.image,) if item.image is not None else ()
    request = build_map_request(plan, instruction, item.text, images=images)
    reply = await model.complete(request)
    if plan.schema is None:
        return reply.rstrip()  # plain mode: keep the reply, only trim trailing whitespace
    try:
        return validate_and_coerce(reply, plan.schema)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        repaired = await model.complete(repair)
        return validate_and_coerce(repaired, plan.schema)  # a second failure → Skipped


def _write(writer: ResultWriter, *, structured: bool, value: str | Mapping[str, object]) -> None:
    if structured and not isinstance(value, str):
        writer.write_record(value)
    else:
        writer.write_text(value if isinstance(value, str) else str(value))
