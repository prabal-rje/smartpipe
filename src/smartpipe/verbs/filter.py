"""The ``filter`` verb: semantic grep (spec §3.2).

Judges each item against a natural-language condition and emits the items that
match — byte-for-byte unchanged, in input order, a strict subset of the input.
``--not`` inverts. Zero matches is success (exit 0), unlike grep: an empty result
is a valid result.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Protocol

from smartpipe.cli import screens
from smartpipe.core.errors import ExitCode, ItemError, UsageFault
from smartpipe.engine.chunking import estimate_tokens, is_context_overflow, split_text
from smartpipe.engine.prompts import (
    JUDGE_SCHEMA,
    build_filter_request,
    build_repair_request,
    has_brace,
    interpolate_fields,
    parse_prompt,
    reject_comma_groups,
    render_input,
)
from smartpipe.engine.runner import Done, run_ordered
from smartpipe.engine.schema import validate_and_coerce
from smartpipe.io import diagnostics, readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source
from smartpipe.io.progress import make_stderr_spinner
from smartpipe.io.writers import OutputFormat
from smartpipe.verbs.common import (
    ModelSlot,
    WindowGate,
    breaker_policy,
    ensure_text,
    interrupted_exit_code,
    make_failover,
    outcome_exit_code,
    prepend,
)
from smartpipe.verbs.convert import Converter, make_converter
from smartpipe.verbs.oversize import judge_any, machine_cut, resplit_halves, resplit_note

if TYPE_CHECKING:
    from typing import TextIO

    from smartpipe.engine.prompts import Token
    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.io.writers import ResultWriter, TextSink
    from smartpipe.models.base import ChatModel, ModelRef
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.stt import RemoteTranscriber

__all__ = ["FilterContext", "FilterRequest", "run_filter"]

_PROMPT_OVERHEAD_TOKENS = 500  # condition + judge wrapper headroom


@dataclass(frozen=True, slots=True)
class FilterRequest:
    condition: str
    invert: bool
    model_flag: str | None
    concurrency_flag: int | None
    input: InputSpec = STDIN
    allow_captions: bool = False  # cloud conversions opt-in (D33)
    fallback_flag: str | None = None  # --fallback-model: chat failover when the breaker trips
    whole: bool = False  # --whole: refuse oversized items instead of chunk-judging (D26 v2)
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion (item 40)


class FilterContext(Protocol):
    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> RemoteTranscriber | None: ...
    def document_parser(self, flag: str | None = None) -> DocumentParser | None: ...
    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    def fallback_ref(self, flag: str | None = None) -> ModelRef | None: ...
    async def fallback_chat_model(self, ref: ModelRef) -> ChatModel: ...
    async def context_window(self, ref: ModelRef) -> int | None: ...
    def concurrency(self, flag: int | None = None) -> int: ...
    def writer(
        self, output_flag: OutputFormat, *, structured: bool, stdout: TextSink
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
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    parser = context.document_parser(request.ocr_model_flag)  # the ocr-model role (item 40)
    ocr = readers.OcrIngest(parser, log) if parser is not None else None
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop, ocr=ocr)
    model = await context.chat_model(request.model_flag)
    slot = ModelSlot(model)
    fallback = context.fallback_ref(request.fallback_flag)  # embed refs refused here (free)
    spinner = make_stderr_spinner()
    # the arbiter: result writes pause the status line, so they never interleave
    writer = context.writer(OutputFormat.AUTO, structured=False, stdout=spinner.guard(stdout))
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

    spinner.start(total=total)

    converter = make_converter(
        model,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(model.ref),
        ocr=parser,
    )
    gate = WindowGate(
        provider=model.ref.provider,
        model_name=model.ref.name,
        overhead=_PROMPT_OVERHEAD_TOKENS,
        window=partial(context.context_window, model.ref),
    )

    async def worker(item: Item) -> tuple[Item, bool]:
        current = slot.current  # captured per item: the failover swaps wholesale

        async def judge_chunk(chunk: str) -> bool:
            return await _judge(
                current, tokens, replace(item, text=chunk), log, converter, whole=False
            )

        over = await gate.budget_for_oversized(item.text, item.media)
        if over is None:
            try:
                matched = await _judge(current, tokens, item, log, converter)
            except ItemError as exc:
                if (
                    request.whole
                    or not is_context_overflow(str(exc))
                    or not machine_cut(item.source)
                ):
                    raise
                # item 3: the wire rejected the estimate on a MACHINE-cut item
                # — halve, judge the halves ANY-true; user cuts stay errors
                halves = resplit_halves(item.text, cause=exc)
                diagnostics.note(resplit_note(describe_source(item.source)))
                matched = await judge_any(
                    halves,
                    judge_chunk,
                    where=describe_source(item.source),
                    estimate=estimate_tokens(item.text),
                )
        elif request.whole:
            # --whole: the old D26 refusal — reproducibility beats handling
            raise ItemError(gate.refusal(over))
        else:
            # D26 v2: judge the chunks, ANY match keeps the whole item (--not
            # inverts after), early exit on the first true chunk — disclosed
            matched = await judge_any(
                split_text(item.text, over.budget),
                judge_chunk,
                where=describe_source(item.source),
                estimate=over.estimate,
            )
        slot.tally(str(current.ref))
        return item, matched

    policy = breaker_policy(model.ref.provider)
    failover = (
        make_failover(
            slot, partial(context.fallback_chat_model, fallback), limit=policy.transport_limit
        )
        if fallback is not None
        else None
    )
    judged = 0
    matches = 0
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
        log.finish()
    if slot.switched:
        diagnostics.note(slot.receipt())  # the seam stays visible (item 11)
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=judged, skipped=skipped)
        return interrupted_exit_code(done=judged, skipped=skipped)
    return outcome_exit_code(done=judged, skipped=skipped)


def _emit_match(writer: ResultWriter, item: Item) -> None:
    # In whole-file mode the useful output is the filename, not the extracted
    # document text (rank/keep files → get paths back, the Unix behavior —
    # spec §8 / stage-07). Row/line cuts from a file (--as jsonl/lines) are
    # records, not files: they pass through like stdin rows.
    if item.source.kind == "file" and item.source.cut == "file":
        writer.write_text(item.source.name)
    else:
        writer.write_passthrough(item)


async def _judge(
    model: ChatModel,
    tokens: tuple[Token, ...],
    item: Item,
    log: diagnostics.DegradationLog,
    converter: Converter,
    *,
    whole: bool = True,
) -> bool:
    had_media = bool(item.media)  # conversions land in .text, not in .data
    item = await ensure_text(item, log=log, converter=converter)  # D33 ladder
    condition = interpolate_fields(tokens, item.data)  # ItemError → skip-and-warn
    # item 57: a whole record judges as its rendered fields; a chunk (or a
    # converted media item, whose transcript lives only in .text) as its text
    payload = render_input(item) if whole and not had_media else render_input(item.text)
    request = build_filter_request(condition, payload)
    reply = await model.complete(request)
    try:
        verdict = validate_and_coerce(reply, JUDGE_SCHEMA)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        repaired = await model.complete(repair)
        verdict = validate_and_coerce(repaired, JUDGE_SCHEMA)  # second failure → Skipped
    return bool(verdict["match"])
