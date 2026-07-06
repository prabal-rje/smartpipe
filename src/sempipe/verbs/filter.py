"""The ``filter`` verb: semantic grep (spec §3.2).

Judges each item against a natural-language condition and emits the items that
match — byte-for-byte unchanged, in input order, a strict subset of the input.
``--not`` inverts. Zero matches is success (exit 0), unlike grep: an empty result
is a valid result.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.cli import screens
from sempipe.core.errors import ExitCode, ItemError, UsageFault
from sempipe.engine.prompts import (
    JUDGE_SCHEMA,
    build_filter_request,
    build_repair_request,
    has_brace,
    interpolate_fields,
    parse_prompt,
    reject_comma_groups,
)
from sempipe.engine.runner import Done, FailurePolicy, run_ordered
from sempipe.engine.schema import validate_and_coerce
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.progress import make_stderr_spinner
from sempipe.io.writers import OutputFormat
from sempipe.verbs.common import (
    ensure_text,
    interrupted_exit_code,
    outcome_exit_code,
    prepend,
)

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.engine.prompts import Token
    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import ResultWriter
    from sempipe.models.base import ChatModel

__all__ = ["FilterContext", "FilterRequest", "run_filter"]


@dataclass(frozen=True, slots=True)
class FilterRequest:
    condition: str
    invert: bool
    model_flag: str | None
    concurrency_flag: int | None
    input: InputSpec = STDIN


class FilterContext(Protocol):
    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...
    def writer(
        self, output_flag: OutputFormat, *, structured: bool, stdout: TextIO
    ) -> ResultWriter: ...


async def run_filter(
    request: FilterRequest,
    context: FilterContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    tokens = parse_prompt(request.condition)  # UsageFault on bad grammar
    reject_comma_groups(tokens)  # UsageFault: comma-braces are map-only
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    model = await context.chat_model(request.model_flag)
    writer = context.writer(OutputFormat.AUTO, structured=False, stdout=stdout)
    concurrency = context.concurrency(request.concurrency_flag)

    # First-item brace check (streaming can't see "all items" up front): the common
    # mistake — braces over a plain-text pipe — still fails fast, before any model
    # call; a mixed stream after a JSON first line skips per item instead.
    first = await anext(items_iter, None)
    if first is None:
        if stop is not None and stop.is_set():
            diagnostics.interrupted_summary(processed=0, skipped=0)
            return interrupted_exit_code(done=0, skipped=0)
        return ExitCode.OK
    if has_brace(tokens) and first.data is None:
        raise UsageFault(screens.FIELD_REF_ON_PLAIN_INPUT)  # exit 64, zero model calls
    items_iter = prepend(first, items_iter)

    spinner = make_stderr_spinner()
    spinner.start(total=total)

    async def worker(item: Item) -> tuple[Item, bool]:
        return item, await _judge(model, tokens, item)

    judged = 0
    matches = 0
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
                judged += 1
                item, matched = outcome.value
                if matched:
                    matches += 1
                    spinner.matched = matches  # the status line's "N matched" segment
                if matched is not request.invert:  # kept (or, with --not, dropped)
                    _emit_match(writer, item)
            else:  # Skipped
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=judged, skipped=skipped)
        return interrupted_exit_code(done=judged, skipped=skipped)
    return outcome_exit_code(done=judged, skipped=skipped)


def _emit_match(writer: ResultWriter, item: Item) -> None:
    # In file mode the useful output is the filename, not the extracted document text
    # (rank/keep files → get paths back, the Unix behavior — spec §8 / stage-07).
    if item.source.kind == "file":
        writer.write_text(item.source.name)
    else:
        writer.write_passthrough(item)


async def _judge(model: ChatModel, tokens: tuple[Token, ...], item: Item) -> bool:
    item = await ensure_text(item)  # image skips; audio transcribes (D20 rung 2)
    condition = interpolate_fields(tokens, item.data)  # ItemError → skip-and-warn
    request = build_filter_request(condition, item.text)
    reply = await model.complete(request)
    try:
        verdict = validate_and_coerce(reply, JUDGE_SCHEMA)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        repaired = await model.complete(repair)
        verdict = validate_and_coerce(repaired, JUDGE_SCHEMA)  # second failure → Skipped
    return bool(verdict["match"])
