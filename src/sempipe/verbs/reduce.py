"""The ``reduce`` verb: synthesize many items into one (spec §3.5).

The headline feature is invisible recursion: when the input exceeds the model's
context, sempipe chunks it, summarizes each chunk into dense notes, and recurses on
the notes — no flags, no strategy to choose. ``--group-by`` runs one reduction per
group; ``--schema`` shapes the final output; ``--verbose`` shows the chunking tree.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, ItemError, UsageFault
from sempipe.engine.chunking import (
    budget_for,
    chunk_indices,
    estimate_tokens,
    fits_in_one,
    halve,
    is_context_overflow,
)
from sempipe.engine.prompts import (
    build_reduce_final,
    build_reduce_intermediate,
    build_repair_request,
    has_brace,
    interpolate_fields,
    parse_prompt,
    reject_comma_groups,
    to_instruction,
)
from sempipe.engine.schema import load_schema, validate_and_coerce
from sempipe.engine.windows import Window, WindowBuffer, WindowPolicy
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.writers import OutputFormat
from sempipe.verbs.common import (
    ensure_text,
    interrupted_exit_code,
    outcome_exit_code,
    resolve_schema,
)

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from pathlib import Path
    from typing import TextIO

    from sempipe.engine.prompts import Token
    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import ResultWriter
    from sempipe.models.base import ChatModel, ModelRef

__all__ = ["ReduceContext", "ReduceRequest", "Reducer", "run_reduce"]

_OVERHEAD_TOKENS = 300  # reserve room for the prompt template + response


@dataclass(frozen=True, slots=True)
class ReduceRequest:
    prompt: str
    schema_path: Path | None
    group_by: str | None
    model_flag: str | None
    concurrency_flag: int | None
    verbose: bool
    input: InputSpec = STDIN
    window: int | None = None  # --window N: stream mode, one reduce per window
    every: int | None = None  # --every M: sliding stride (default: tumbling)
    fields: tuple[str, ...] | None = None  # --fields: project structured output
    schema_dsl: str | None = None  # --schema-from (rung 3, D22)


class ReduceContext(Protocol):
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


async def run_reduce(
    request: ReduceRequest,
    context: ReduceContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    tokens = parse_prompt(request.prompt)
    reject_comma_groups(tokens)
    if has_brace(tokens) and request.group_by is None:
        raise UsageFault(
            "field references like {x} in reduce only work with --group-by "
            "(where {field} means the group's value)"
        )
    schema = resolve_schema(request.schema_path, request.schema_dsl, loader=load_schema)
    if request.every is not None and request.window is None:
        raise UsageFault(
            "--every only makes sense with --window\n"
            "  --window N summarizes every N lines; --every M makes those windows slide.\n"
            '  Example: tail -f app.log | sempipe reduce --window 100 --every 20 "error trend?"'
        )
    if request.window is not None:
        return await _run_windowed(request, tokens, schema, context, stdin, stdout, stop)
    items_iter, _total = readers.resolve_items(request.input, stdin)
    collected = [item async for item in items_iter]  # whole-set verbs need everything
    items: list[Item] = []
    media_skipped = 0
    for candidate in collected:
        try:
            items.append(await ensure_text(candidate))  # audio transcribes; images skip
        except ItemError as exc:
            diagnostics.warn(f"skipped: {describe_source(candidate.source)} ({exc})")
            media_skipped += 1
    model = await context.chat_model(request.model_flag)
    concurrency = context.concurrency(request.concurrency_flag)
    structured = schema is not None
    writer = context.writer(
        OutputFormat.AUTO, structured=structured, stdout=stdout, fields=request.fields
    )

    if not items:
        return ExitCode.OK

    reducer = Reducer(
        model=model,
        budget=budget_for(model.ref.provider, prompt_overhead=_OVERHEAD_TOKENS),
        concurrency=concurrency,
        verbose=request.verbose,
        window_budget=_window_budget(context, model),
    )
    try:
        if request.group_by is not None:
            await _run_grouped(reducer, request, tokens, schema, items, writer)
        else:
            await _run_single(reducer, tokens, schema, items, writer)
    finally:
        writer.flush()
    if reducer.produced == 0:
        return ExitCode.ALL_FAILED
    return ExitCode.PARTIAL if (reducer.skipped or media_skipped) else ExitCode.OK


async def _run_single(
    reducer: Reducer,
    tokens: tuple[Token, ...],
    schema: Mapping[str, object] | None,
    items: list[Item],
    writer: ResultWriter,
) -> None:
    instruction = to_instruction(tokens)
    try:
        result = await reducer.reduce(instruction, schema, [item.text for item in items])
    except ItemError as exc:
        diagnostics.warn(f"reduce failed: {exc}")
        return
    _emit(writer, structured=schema is not None, result=result)
    reducer.produced += 1


async def _run_windowed(
    request: ReduceRequest,
    tokens: tuple[Token, ...],
    schema: Mapping[str, object] | None,
    context: ReduceContext,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
) -> ExitCode:
    """Stream mode (stage-08 §4.2): one reduce per window, emitted as it lands;
    the trailing partial window is flushed so Ctrl+C never discards buffered lines."""
    assert request.window is not None
    if request.group_by is not None:
        raise UsageFault(
            "--window can't combine with --group-by (windows and groups don't compose)"
        )
    if request.input.patterns or request.input.from_files:
        raise UsageFault(
            "reduce --window reads a stream from stdin — it can't combine with --in\n"
            "  File inputs are a finite batch. Drop --window, or pipe the stream in."
        )
    try:
        policy = WindowPolicy(size=request.window, every=request.every or request.window)
    except ValueError as exc:
        raise UsageFault(str(exc)) from exc

    model = await context.chat_model(request.model_flag)
    writer = context.writer(
        OutputFormat.AUTO, structured=True, stdout=stdout, fields=request.fields
    )
    instruction = to_instruction(tokens)
    reducer = Reducer(
        model=model,
        budget=budget_for(model.ref.provider, prompt_overhead=_OVERHEAD_TOKENS),
        concurrency=context.concurrency(request.concurrency_flag),
        verbose=request.verbose,
        window_budget=_window_budget(context, model),
    )
    buffer: WindowBuffer[str] = WindowBuffer(policy)
    produced = 0
    failed = 0

    async def emit(window: Window[str]) -> None:
        nonlocal produced, failed
        try:
            result = await reducer.reduce(instruction, schema, list(window.items))
        except ItemError as exc:
            diagnostics.warn(f"skipped: window ending at line {window.end_index} ({exc})")
            failed += 1
            return
        record: dict[str, object] = {"window_end": window.end_index, "result": result}
        if window.partial:
            record["partial"] = True
        writer.write_record(record)
        produced += 1

    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
    try:
        async for item in items_iter:
            if item.media is not None:
                try:
                    item = await ensure_text(item)  # audio transcribes; images skip
                except ItemError as exc:
                    diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
                    failed += 1
                    continue
            window = buffer.push(item.text)
            if window is not None:
                await emit(window)
        tail = buffer.flush()
        if tail is not None:
            await emit(tail)
    finally:
        writer.flush()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=produced, skipped=failed)
        return interrupted_exit_code(done=produced, skipped=failed)
    return outcome_exit_code(done=produced, skipped=failed)


async def _run_grouped(
    reducer: Reducer,
    request: ReduceRequest,
    tokens: tuple[Token, ...],
    schema: Mapping[str, object] | None,
    items: list[Item],
    writer: ResultWriter,
) -> None:
    assert request.group_by is not None
    for value, group_items in _group(items, request.group_by, reducer):
        instruction = interpolate_fields(tokens, {request.group_by: value})
        try:
            result = await reducer.reduce(instruction, schema, [item.text for item in group_items])
        except ItemError as exc:
            diagnostics.warn(f"reduce failed for group {value!r}: {exc}")
            reducer.skipped += 1
            continue
        writer.write_record({"group": value, "result": result})
        reducer.produced += 1


def _group(items: list[Item], field_name: str, reducer: Reducer) -> list[tuple[object, list[Item]]]:
    order: list[str] = []
    groups: dict[str, tuple[object, list[Item]]] = {}
    for item in items:
        if item.data is None or field_name not in item.data:
            diagnostics.warn(f"skipped: {describe_source(item.source)} (no field '{field_name}')")
            reducer.skipped += 1
            continue
        value = item.data[field_name]
        key = value if isinstance(value, str) else repr(value)
        if key not in groups:
            groups[key] = (value, [])
            order.append(key)
        groups[key][1].append(item)
    return [groups[key] for key in order]


def _emit(writer: ResultWriter, *, structured: bool, result: str | Mapping[str, object]) -> None:
    if structured and not isinstance(result, str):
        writer.write_record(result)
    else:
        writer.write_text(result if isinstance(result, str) else str(result))


def _window_budget(context: ReduceContext, model: ChatModel) -> Callable[[], Awaitable[int | None]]:
    async def refreshed() -> int | None:
        window = await context.context_window(model.ref)
        if window is None:
            return None
        return budget_for(model.ref.provider, prompt_overhead=_OVERHEAD_TOKENS, window=window)

    return refreshed


@dataclass
class Reducer:
    model: ChatModel
    budget: int
    concurrency: int
    verbose: bool
    skipped: int = 0
    produced: int = 0
    bisection_noted: bool = False
    window_budget: Callable[[], Awaitable[int | None]] | None = None
    probed: bool = False

    async def reduce(
        self, instruction: str, schema: Mapping[str, object] | None, texts: Sequence[str]
    ) -> str | Mapping[str, object]:
        trace = [len(texts)]
        current = list(texts)
        semaphore = asyncio.Semaphore(self.concurrency)
        await self._widen_if_possible(current)
        while True:
            while not fits_in_one([estimate_tokens(t) for t in current], self.budget):
                chunks = chunk_indices([estimate_tokens(t) for t in current], self.budget)
                trace.append(len(chunks))
                current = await self._reduce_level(instruction, current, chunks, semaphore)
                if not current:
                    raise ItemError("every chunk failed to reduce")
            try:
                if len(trace) > 1:
                    trace.append(1)
                    if self.verbose:
                        diagnostics.note(_trace_line(trace))
                return await self._final(instruction, schema, current)
            except ItemError as exc:
                if not (is_context_overflow(str(exc)) and len(current) > 1):
                    raise
                # the synthesis call itself overflowed: collapse one more level
                self._note_bisection()
                first, second = halve(tuple(range(len(current))))
                current = await self._reduce_level(instruction, current, (first, second), semaphore)
                if not current:
                    raise ItemError("every chunk failed to reduce") from exc

    async def _reduce_level(
        self,
        goal: str,
        texts: list[str],
        chunks: tuple[tuple[int, ...], ...],
        semaphore: asyncio.Semaphore,
    ) -> list[str]:
        async def reduce_chunk(chunk: tuple[int, ...]) -> list[str]:
            request = build_reduce_intermediate(goal, [texts[i] for i in chunk])
            try:
                async with semaphore:  # released before any bisection recursion
                    return [await self.model.complete(request)]
            except ItemError as exc:
                if is_context_overflow(str(exc)) and len(chunk) > 1:
                    # D26: the wire said this chunk is too big — the estimate
                    # lied, so split at item boundaries and retry both halves
                    self._note_bisection()
                    first, second = halve(chunk)
                    return [*await reduce_chunk(first), *await reduce_chunk(second)]
                assert chunk, "chunk_indices never yields an empty chunk"
                diagnostics.warn(
                    f"skipped: chunk over items {chunk[0] + 1}-{chunk[-1] + 1} ({exc})"
                )
                self.skipped += 1
                return []

        tasks = [asyncio.create_task(reduce_chunk(chunk)) for chunk in chunks]
        notes = [note for task in tasks for note in await task]  # awaited in order
        return notes

    async def _widen_if_possible(self, texts: list[str]) -> None:
        """D26 layer 1: the table budget looks too small — ask the provider for
        the real window, once. A bigger true window means fewer (or no) levels."""
        if self.window_budget is None or self.probed:
            return
        if fits_in_one([estimate_tokens(t) for t in texts], self.budget):
            return
        self.probed = True
        refreshed = await self.window_budget()
        if refreshed is not None and refreshed > self.budget:
            self.budget = refreshed

    def _note_bisection(self) -> None:
        if not self.bisection_noted:
            self.bisection_noted = True
            diagnostics.note(
                "a chunk overflowed the model's window — splitting further and retrying"
            )

    async def _final(
        self, instruction: str, schema: Mapping[str, object] | None, texts: list[str]
    ) -> str | Mapping[str, object]:
        request = build_reduce_final(instruction, texts, schema)
        reply = await self.model.complete(request)
        if schema is None:
            return reply.strip()
        try:
            return validate_and_coerce(reply, schema)
        except ItemError as first_error:
            repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
            return validate_and_coerce(await self.model.complete(repair), schema)


def _trace_line(trace: list[int]) -> str:
    tail = "".join(f" → {count}" for count in trace[2:])
    return f"reduce: {trace[0]:,} items → {trace[1]} chunks{tail}"
