"""The ``extend`` verb: enrich, don't replace (D38/02, KQL ``extend``).

map's machinery with a merge at the emit edge: the input record's fields
survive, the extracted fields land beside them. The verb every dataset owner
(P1/P11/P12/P13) needs so results flow back into their existing pipelines.
"""

from __future__ import annotations

import asyncio
from collections.abc import Mapping
from dataclasses import dataclass
from functools import partial
from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode, ItemError, UsageFault
from sempipe.engine.prompts import parse_prompt, plan_map, to_instruction
from sempipe.engine.runner import Done, FailurePolicy, run_ordered
from sempipe.engine.schema import load_schema
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.progress import make_stderr_spinner
from sempipe.verbs.common import (
    WindowGate,
    interrupted_exit_code,
    outcome_exit_code,
    resolve_schema,
)
from sempipe.verbs.map import MapContext, map_one

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import OutputFormat

__all__ = ["ExtendRequest", "base_fields", "run_extend"]

_PROMPT_OVERHEAD_TOKENS = 500  # instruction + wrapper + reply headroom (map's)

EXTEND_NEEDS_FIELDS = (
    "extend adds fields — name them in braces or pass --schema\n"
    '  Example: sempipe extend "Add {sentiment enum(pos, neg, neutral)}"\n'
    "  Plain-text transformation? That's map."
)

# media transport keys (D27/D32): consumed by the model, poison to re-emit
_TRANSPORT_KEYS = frozenset({"audio_b64", "image_b64", "video_b64", "parts", "mime"})


@dataclass(frozen=True, slots=True)
class ExtendRequest:
    prompt: str
    schema_path: Path | None
    model_flag: str | None
    output: OutputFormat
    concurrency_flag: int | None
    input: InputSpec = STDIN
    fields: tuple[str, ...] | None = None
    schema_dsl: str | None = None
    tally_field: str | None = None
    explode_field: str | None = None


async def run_extend(
    request: ExtendRequest,
    context: MapContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    tokens = parse_prompt(request.prompt, allow_descriptions=True)
    schema = resolve_schema(request.schema_path, request.schema_dsl, loader=load_schema)
    plan = plan_map(tokens, schema=schema)
    if plan.mode != "structured":
        raise UsageFault(EXTEND_NEEDS_FIELDS)  # exit 64, zero model calls
    instruction = to_instruction(tokens)
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    model = await context.chat_model(request.model_flag)
    writer = context.writer(request.output, structured=True, stdout=stdout, fields=request.fields)
    concurrency = context.concurrency(request.concurrency_flag)

    tally = None
    if request.tally_field is not None:
        from sempipe.engine.tally import Tally

        tally = Tally(request.tally_field)

    spinner = make_stderr_spinner()
    spinner.start(total=total)

    log = diagnostics.DegradationLog()
    gate = WindowGate(
        provider=model.ref.provider,
        model_name=model.ref.name,
        overhead=_PROMPT_OVERHEAD_TOKENS,
        window=partial(context.context_window, model.ref),
    )

    async def worker(item: Item) -> tuple[Item, Mapping[str, object]]:
        budget = await gate.budget_for_oversized(item.text)
        if budget is not None:
            raise ItemError(gate.refusal(item.text, budget))  # D26: no silent chunking
        result = await map_one(model, plan, instruction, item, log)
        assert isinstance(result, Mapping)  # structured mode: validated against the schema
        return item, result

    done = 0
    skipped = 0
    overwritten: set[str] = set()  # disclosed once per field
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
                item, extracted = outcome.value
                base = base_fields(item)
                for collision in sorted(base.keys() & extracted.keys()):
                    if collision not in overwritten:
                        overwritten.add(collision)
                        diagnostics.note(f"overwriting '{collision}' on incoming records")
                merged: dict[str, object] = {**base, **extracted}
                for row in _rows(merged, request.explode_field):
                    writer.write_record(row)
                    if tally is not None:
                        tally.add(row)
                        spinner.extra = tally.live_segment()
                done += 1
            else:  # Skipped
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
        log.finish()
    if tally is not None and tally.counts:
        diagnostics.note(tally.final_line())
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=done, skipped=skipped)
        return interrupted_exit_code(done=done, skipped=skipped)
    return outcome_exit_code(done=done, skipped=skipped)


def base_fields(item: Item) -> dict[str, object]:
    """The record to enrich: its own fields, or {"text": …} for plain lines.
    Media-transport payloads are dropped — consumed by the model, poison to
    re-emit (megabytes of b64 in every output row)."""
    if item.data is None:
        return {"text": item.text}
    if not item.media:
        return dict(item.data)
    return {key: value for key, value in item.data.items() if key not in _TRANSPORT_KEYS}


def _rows(merged: Mapping[str, object], explode_field: str | None) -> list[Mapping[str, object]]:
    if explode_field is None:
        return [merged]
    from sempipe.engine.tally import explode_record

    return list(explode_record(merged, explode_field))
