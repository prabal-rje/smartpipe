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

from smartpipe.core.errors import ExitCode, ItemError, UsageFault
from smartpipe.engine.chunking import is_context_overflow
from smartpipe.engine.prompts import parse_prompt, plan_map, to_instruction
from smartpipe.engine.runner import Done, run_ordered
from smartpipe.engine.schema import load_schema
from smartpipe.io import diagnostics, readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source
from smartpipe.io.progress import make_stderr_spinner
from smartpipe.verbs.common import (
    ModelSlot,
    WindowGate,
    breaker_policy,
    interrupted_exit_code,
    make_failover,
    outcome_exit_code,
    resolve_schema,
)
from smartpipe.verbs.map import MapContext, map_one, print_dry_run
from smartpipe.verbs.oversize import machine_cut, transform_oversized, transform_resplit

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.io.writers import OutputFormat

__all__ = ["ExtendRequest", "base_fields", "run_extend"]

_PROMPT_OVERHEAD_TOKENS = 500  # instruction + wrapper + reply headroom (map's)

EXTEND_NEEDS_FIELDS = (
    "extend adds fields — name them in braces or pass --schema\n"
    '  Example: smartpipe extend "Add {sentiment enum(pos, neg, neutral)}"\n'
    "  Plain-text transformation? That's map."
)

# the media transport field (D27/D32, item 12): consumed by the model, poison
# to re-emit (megabytes of base64 in every output row)
_TRANSPORT_KEYS = frozenset({"__media"})


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
    frame_every: float | None = None  # D43
    max_frames: int | None = None  # D43
    keep_invalid: bool = False  # --keep-invalid: failure markers merge onto the base record
    dry_run: bool = False  # --dry-run: print the composed first request, spend nothing
    fallback_flag: str | None = None  # --fallback-model: chat failover when the breaker trips
    bare: bool = False  # --bare: strip __ metadata from record output (item 18)
    full: bool = False  # --full: disable the TTY preview's truncation (item 19)
    whole: bool = False  # --whole: refuse oversized items instead of auto-chunking (D26 v2)


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
    if request.dry_run:  # before model resolution: a dry run is free even pre-setup
        return await print_dry_run(plan, instruction, items_iter, stdout=stdout)
    model = await context.chat_model(request.model_flag)
    slot = ModelSlot(model)
    fallback = context.fallback_ref(request.fallback_flag)  # embed refs refused here (free)
    spinner = make_stderr_spinner()
    # the arbiter: result writes pause the status line, so they never interleave
    writer = context.writer(
        request.output,
        structured=True,
        stdout=spinner.guard(stdout),
        fields=request.fields,
        bare=request.bare,
        full=request.full,
    )
    concurrency = context.concurrency(request.concurrency_flag)

    tally = None
    if request.tally_field is not None:
        from smartpipe.engine.tally import Tally

        tally = Tally(request.tally_field)

    spinner.start(total=total)

    log = diagnostics.DegradationLog()
    gate = WindowGate(
        provider=model.ref.provider,
        model_name=model.ref.name,
        overhead=_PROMPT_OVERHEAD_TOKENS,
        window=partial(context.context_window, model.ref),
    )

    async def worker(item: Item) -> tuple[Item, Mapping[str, object]]:
        current = slot.current  # captured per item: the failover swaps wholesale
        over = await gate.budget_for_oversized(item.text, item.media)
        if over is not None and request.whole:
            # --whole: the old D26 refusal — reproducibility beats handling
            raise ItemError(gate.refusal(over))
        if over is not None:
            # D26 v2: extract per chunk, then ONE merge call against the same schema
            result = await transform_oversized(
                current, plan, instruction, item, over, keep_invalid=request.keep_invalid
            )
        else:
            try:
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
            except ItemError as exc:
                if (
                    request.whole
                    or not is_context_overflow(str(exc))
                    or not machine_cut(item.source)
                ):
                    raise
                # item 3: the wire rejected the estimate on a MACHINE-cut item
                result = await transform_resplit(
                    current,
                    plan,
                    instruction,
                    item,
                    keep_invalid=request.keep_invalid,
                    cause=exc,
                )
        assert isinstance(result, Mapping)  # structured mode: validated against the schema
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
    overwritten: set[str] = set()  # disclosed once per field
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
    if slot.switched:
        diagnostics.note(slot.receipt())  # the seam stays visible (item 11)
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
    from smartpipe.engine.tally import explode_record

    return list(explode_record(merged, explode_field))
