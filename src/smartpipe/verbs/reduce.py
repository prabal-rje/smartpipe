"""The ``reduce`` verb: synthesize many items into one (spec §3.5).

The headline feature is invisible recursion: when the input exceeds the model's
context, smartpipe chunks it, summarizes each chunk into dense notes, and recurses on
the notes — no flags, no strategy to choose. ``--group-by`` runs one reduction per
group; ``--schema`` shapes the final output; ``--verbose`` shows the chunking tree.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from smartpipe.core.errors import ExitCode, ItemError, UnsentError, UsageFault
from smartpipe.engine.chunking import (
    budget_for,
    chunk_indices,
    estimate_tokens,
    fits_in_one,
    halve,
    is_context_overflow,
    split_text,
)
from smartpipe.engine.prompts import (
    build_reduce_final,
    build_reduce_intermediate,
    build_repair_request,
    has_brace,
    interpolate_fields,
    parse_prompt,
    reject_comma_groups,
    render_input,
    to_instruction,
)
from smartpipe.engine.schema import load_schema, validate_and_coerce
from smartpipe.engine.windows import Window, WindowBuffer, WindowPolicy
from smartpipe.io import diagnostics, readers, source_accounting
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source, source_record
from smartpipe.io.writers import OutputFormat
from smartpipe.verbs.common import (
    ensure_text,
    interrupted_exit_code,
    outcome_exit_code,
    resolve_schema,
)
from smartpipe.verbs.convert import make_converter
from smartpipe.verbs.oversize import MAX_BISECT_DEPTH

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping, Sequence
    from pathlib import Path
    from typing import TextIO

    from smartpipe.engine.prompts import Token
    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.io.writers import ResultWriter
    from smartpipe.models.base import ChatModel, ModelRef
    from smartpipe.models.budget import CallBudget
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.stt import Transcriber

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
    allow_captions: bool = False  # cloud conversions opt-in (D33)
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion (item 40)


class ReduceContext(Protocol):
    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> Transcriber | None: ...
    def document_parser(self, flag: str | None = None) -> DocumentParser | None: ...
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
    budget: CallBudget | None = None,
) -> ExitCode:
    tokens = parse_prompt(request.prompt, allow_paths=True)
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
            '  Example: tail -f app.log | smartpipe reduce --window 100 --every 20 "error trend?"'
        )
    concurrency = context.concurrency(request.concurrency_flag)
    if request.window is not None:
        return await _run_windowed(
            request, tokens, schema, context, stdin, stdout, stop, concurrency, budget
        )
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    items_iter, _total = readers.resolve_items(request.input, stdin, ocr=ocr, budget=budget)
    collected = [item async for item in items_iter]  # whole-set verbs need everything
    if not collected:
        log.finish()
        return outcome_exit_code(done=0, skipped=0, failed=0)
    rows: list[tuple[Item, str]] = []  # each item with its <input> payload (item 57)
    media_skipped = 0
    media_failed = 0
    sources = source_accounting.SourceCounter()
    model = await context.chat_model(request.model_flag)
    converter = make_converter(
        model,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(model.ref),
        ocr=ocr,
    )
    for candidate in collected:
        try:
            converted = await ensure_text(candidate, log=log, converter=converter)
        except ItemError as exc:
            diagnostics.warn(f"skipped: {describe_source(candidate.source)} ({exc})")
            media_skipped += 1
            media_failed += int(not isinstance(exc, UnsentError))
            sources.skip(candidate.source, failed=not isinstance(exc, UnsentError))
            continue
        # a converted media item's transcript lives only in .text, not .data
        payload = render_input(converted.text) if candidate.media else render_input(converted)
        rows.append((converted, payload))
    structured = schema is not None
    if not rows:
        log.finish()
        return outcome_exit_code(
            done=0,
            skipped=len(collected),
            failed=media_failed,
            source_counts=sources.counts,
        )

    writer = context.writer(
        OutputFormat.AUTO, structured=structured, stdout=stdout, fields=request.fields
    )

    reducer = Reducer(
        model=model,
        budget=budget_for(model.ref.provider, prompt_overhead=_OVERHEAD_TOKENS),
        concurrency=concurrency,
        verbose=request.verbose,
        window_budget=_window_budget(context, model),
    )
    try:
        if request.group_by is not None:
            succeeded, skipped, failed = await _run_grouped(
                reducer, request, tokens, schema, rows, writer, sources
            )
        else:
            succeeded, skipped, failed = await _run_single(
                reducer,
                tokens,
                schema,
                rows,
                writer,
                sources,
            )
    finally:
        writer.flush()
        log.finish()
    return outcome_exit_code(
        done=succeeded,
        skipped=skipped + media_skipped,
        failed=failed + media_failed,
        source_counts=sources.counts,
    )


async def _run_single(
    reducer: Reducer,
    tokens: tuple[Token, ...],
    schema: Mapping[str, object] | None,
    rows: list[tuple[Item, str]],
    writer: ResultWriter,
    sources: source_accounting.SourceCounter,
) -> tuple[int, int, int]:
    instruction = to_instruction(tokens)
    payloads = [payload for _item, payload in rows]
    try:
        counted = await reducer.reduce_counted(instruction, schema, payloads)
    except _ReductionError as exc:
        diagnostics.warn(f"reduce failed: {exc}")
        _record_reduction_sources(sources, rows, succeeded=frozenset(), failed=exc.failed)
        return 0, len(exc.skipped), len(exc.failed)
    except ItemError as exc:
        diagnostics.warn(f"reduce failed: {exc}")
        failed_sources: frozenset[int] = (
            frozenset() if isinstance(exc, UnsentError) else frozenset(range(len(rows)))
        )
        _record_reduction_sources(
            sources,
            rows,
            succeeded=frozenset(),
            failed=failed_sources,
        )
        return 0, len(payloads), 0 if isinstance(exc, UnsentError) else len(payloads)
    result = counted.value
    if not isinstance(result, str):
        # item 64: a whole-input synthesis carries its summary spine — how
        # many items went in (plain-text results have no record to carry it)
        result = {**result, "__source": {"as": "all", "count": len(payloads)}}
    _emit(writer, structured=schema is not None, result=result)
    reducer.produced += 1
    _record_reduction_sources(
        sources,
        rows,
        succeeded=counted.succeeded,
        failed=counted.failed,
    )
    skipped = len(counted.skipped)
    return len(counted.succeeded), skipped, len(counted.failed)


async def _run_windowed(
    request: ReduceRequest,
    tokens: tuple[Token, ...],
    schema: Mapping[str, object] | None,
    context: ReduceContext,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
    concurrency: int,
    budget: CallBudget | None = None,
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
        concurrency=concurrency,
        verbose=request.verbose,
        window_budget=_window_budget(context, model),
    )
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    converter = make_converter(
        model,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(model.ref),
        ocr=ocr,
    )
    # the buffer holds (payload, spine position) pairs so each emitted window
    # can carry its summary spine (item 64) without the engine knowing items
    buffer: WindowBuffer[tuple[str, int | None, int]] = WindowBuffer(policy)
    produced = 0
    consumed = 0
    represented: set[int] = set()
    attempted_failures: set[int] = set()
    source_items: dict[int, Item] = {}

    async def emit(window: Window[tuple[str, int | None, int]]) -> None:
        nonlocal produced
        try:
            counted = await reducer.reduce_counted(
                instruction,
                schema,
                [payload for payload, _position, _source_id in window.items],
            )
        except _ReductionError as exc:
            diagnostics.warn(f"skipped: window ending at line {window.end_index} ({exc})")
            attempted_failures.update(window.items[index][2] for index in exc.failed)
            return
        except ItemError as exc:
            diagnostics.warn(f"skipped: window ending at line {window.end_index} ({exc})")
            if not isinstance(exc, UnsentError):
                attempted_failures.update(
                    source_id for _payload, _position, source_id in window.items
                )
            return
        represented.update(window.items[index][2] for index in counted.succeeded)
        attempted_failures.update(window.items[index][2] for index in counted.failed)
        record: dict[str, object] = {
            "window_end": window.end_index,
            "result": counted.value,
        }
        if window.partial:
            record["partial"] = True
        record["__source"] = _window_source(window.items)
        writer.write_record(record)
        produced += 1

    items_iter, _total = readers.resolve_items(
        request.input, stdin, stop=stop, ocr=ocr, budget=budget
    )
    try:
        async for item in items_iter:
            source_id = consumed
            consumed += 1
            source_items[source_id] = item
            had_media = bool(item.media)
            if had_media:
                try:
                    item = await ensure_text(item, log=log, converter=converter)
                except ItemError as exc:
                    diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
                    if not isinstance(exc, UnsentError):
                        attempted_failures.add(source_id)
                    continue
            # item 57: windows hold rendered <input> payloads (a converted
            # media item's transcript lives only in .text)
            payload = render_input(item.text) if had_media else render_input(item)
            window = buffer.push((payload, _spine_position(item), source_id))
            if window is not None:
                await emit(window)
        tail = buffer.flush()
        if tail is not None:
            await emit(tail)
    finally:
        writer.flush()
        log.finish()
    skipped_sources = set(range(consumed)) - represented
    failed_sources = skipped_sources & attempted_failures
    sources = source_accounting.SourceCounter()
    for source_id, item in source_items.items():
        if source_id in represented:
            sources.done(item.source)
        else:
            sources.skip(item.source, failed=source_id in failed_sources)
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=produced, skipped=len(skipped_sources))
        return interrupted_exit_code(
            done=len(represented),
            skipped=len(skipped_sources),
            failed=len(failed_sources),
            source_counts=sources.counts,
        )
    return outcome_exit_code(
        done=len(represented),
        skipped=len(skipped_sources),
        failed=len(failed_sources),
        source_counts=sources.counts,
    )


async def _run_grouped(
    reducer: Reducer,
    request: ReduceRequest,
    tokens: tuple[Token, ...],
    schema: Mapping[str, object] | None,
    rows: list[tuple[Item, str]],
    writer: ResultWriter,
    sources: source_accounting.SourceCounter,
) -> tuple[int, int, int]:
    assert request.group_by is not None
    groups, missing = _group(rows, request.group_by)
    succeeded = 0
    skipped = len(missing)
    failed = 0
    for item in missing:
        sources.skip(item.source, failed=False)
    for value, group_rows in groups:
        group_payloads = [payload for _item, payload in group_rows]
        instruction = interpolate_fields(tokens, {request.group_by: value})
        try:
            counted = await reducer.reduce_counted(instruction, schema, group_payloads)
        except _ReductionError as exc:
            diagnostics.warn(f"reduce failed for group {value!r}: {exc}")
            reducer.skipped += 1
            _record_reduction_sources(
                sources,
                group_rows,
                succeeded=frozenset(),
                failed=exc.failed,
            )
            skipped += len(exc.skipped)
            failed += len(exc.failed)
            continue
        except ItemError as exc:
            diagnostics.warn(f"reduce failed for group {value!r}: {exc}")
            reducer.skipped += 1
            failed_sources: frozenset[int] = (
                frozenset() if isinstance(exc, UnsentError) else frozenset(range(len(group_rows)))
            )
            _record_reduction_sources(
                sources,
                group_rows,
                succeeded=frozenset(),
                failed=failed_sources,
            )
            skipped += len(group_payloads)
            failed += 0 if isinstance(exc, UnsentError) else len(group_payloads)
            continue
        writer.write_record(
            {
                "group": value,
                "result": counted.value,
                # item 64: the group's summary spine — which group, how many items
                "__source": {"as": "group", "group": value, "count": len(group_payloads)},
            }
        )
        reducer.produced += 1
        _record_reduction_sources(
            sources,
            group_rows,
            succeeded=counted.succeeded,
            failed=counted.failed,
        )
        succeeded += len(counted.succeeded)
        skipped += len(counted.skipped)
        failed += len(counted.failed)
    return succeeded, skipped, failed


def _group(
    rows: list[tuple[Item, str]], field_name: str
) -> tuple[list[tuple[object, list[tuple[Item, str]]]], list[Item]]:
    from smartpipe.engine.fieldpath import MISSING, lookup

    order: list[str] = []
    groups: dict[str, tuple[object, list[tuple[Item, str]]]] = {}
    missing: list[Item] = []
    for item, payload in rows:
        value = lookup(item.data, field_name) if item.data is not None else MISSING
        if value is MISSING:
            diagnostics.warn(f"skipped: {describe_source(item.source)} (no field '{field_name}')")
            missing.append(item)
            continue
        key = value if isinstance(value, str) else repr(value)
        if key not in groups:
            groups[key] = (value, [])
            order.append(key)
        groups[key][1].append((item, payload))
    return [groups[key] for key in order], missing


def _record_reduction_sources(
    sources: source_accounting.SourceCounter,
    rows: list[tuple[Item, str]],
    *,
    succeeded: frozenset[int],
    failed: frozenset[int],
) -> None:
    """Project reducer-local positions back onto logical source outcomes."""
    for position, (item, _payload) in enumerate(rows):
        if position in succeeded:
            sources.done(item.source)
        else:
            sources.skip(item.source, failed=position in failed)


def _spine_position(item: Item) -> int | None:
    """The item's 1-based spine position (line/page/segment) where knowable —
    a window's span is read off its members' positions (item 64)."""
    record = source_record(item.source)
    return next(
        (value for key in ("line", "page", "segment") if isinstance(value := record.get(key), int)),
        None,
    )


def _window_source(items: Sequence[tuple[str, int | None, int]]) -> dict[str, object]:
    """Item 64: the window's summary spine — span from the members' spine
    positions where knowable (first and last that carry one); count always."""
    source: dict[str, object] = {"as": "window"}
    positions = [position for _payload, position, _source_id in items if position is not None]
    if positions:
        source["span"] = [positions[0], positions[-1]]
    source["count"] = len(items)
    return source


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


@dataclass(frozen=True, slots=True)
class _ReductionNode:
    text: str
    sources: frozenset[int]


@dataclass(frozen=True, slots=True)
class _ReductionResult:
    value: str | Mapping[str, object]
    succeeded: frozenset[int]
    skipped: frozenset[int]
    failed: frozenset[int]


class _ReductionError(ItemError):
    def __init__(
        self,
        message: str,
        *,
        skipped: frozenset[int],
        failed: frozenset[int],
    ) -> None:
        self.skipped = skipped
        self.failed = failed
        super().__init__(message)


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
        """Compatibility wrapper for callers that only need the synthesis."""
        return (await self.reduce_counted(instruction, schema, texts)).value

    async def reduce_counted(
        self, instruction: str, schema: Mapping[str, object] | None, texts: Sequence[str]
    ) -> _ReductionResult:
        """Synthesize while carrying source provenance through every note."""
        trace = [len(texts)]
        all_sources = frozenset(range(len(texts)))
        current = [
            _ReductionNode(text=text, sources=frozenset((index,)))
            for index, text in enumerate(texts)
        ]
        lost: set[int] = set()
        failed: set[int] = set()
        semaphore = asyncio.Semaphore(self.concurrency)
        await self._widen_if_possible(current)
        single_collapses = 0  # bounded: a lone text may re-split only so often
        while True:
            while not fits_in_one([estimate_tokens(node.text) for node in current], self.budget):
                chunks = chunk_indices(
                    [estimate_tokens(node.text) for node in current], self.budget
                )
                trace.append(len(chunks))
                current = await self._reduce_level(
                    instruction, current, chunks, semaphore, lost, failed
                )
                if not current:
                    raise _ReductionError(
                        "every chunk failed to reduce",
                        skipped=all_sources,
                        failed=frozenset(failed),
                    )
            try:
                if len(trace) > 1:
                    trace.append(1)
                    if self.verbose:
                        diagnostics.note(_trace_line(trace))
                value = await self._final(instruction, schema, [node.text for node in current])
                represented = frozenset(
                    source for node in current for source in node.sources if source not in lost
                )
                return _ReductionResult(
                    value=value,
                    succeeded=represented,
                    skipped=all_sources - represented,
                    failed=frozenset(failed),
                )
            except ItemError as exc:
                if not is_context_overflow(str(exc)):
                    represented = frozenset(
                        source for node in current for source in node.sources if source not in lost
                    )
                    final_failed: frozenset[int] = (
                        frozenset() if isinstance(exc, UnsentError) else represented
                    )
                    raise _ReductionError(
                        str(exc),
                        skipped=all_sources,
                        failed=frozenset(failed) | final_failed,
                    ) from exc
                if len(current) == 1:
                    single_collapses += 1
                    if single_collapses > MAX_BISECT_DEPTH:
                        raise  # the lone text keeps overflowing — stop paying
                # the synthesis call itself overflowed: collapse one more level
                # (a lone text goes through the level's single-chunk path, which
                # bisects its TEXT — item 3 — instead of giving up)
                self._note_bisection()
                groups = halve(tuple(range(len(current)))) if len(current) > 1 else ((0,),)
                current = await self._reduce_level(
                    instruction, current, groups, semaphore, lost, failed
                )
                if not current:
                    raise _ReductionError(
                        "every chunk failed to reduce",
                        skipped=all_sources,
                        failed=frozenset(failed),
                    ) from exc

    async def _reduce_level(
        self,
        goal: str,
        texts: list[_ReductionNode],
        chunks: tuple[tuple[int, ...], ...],
        semaphore: asyncio.Semaphore,
        lost: set[int],
        failed: set[int],
    ) -> list[_ReductionNode]:
        async def reduce_split(node: _ReductionNode, depth: int) -> list[_ReductionNode]:
            """Item 3: ONE item's text still overflowed the wire — bisect the
            TEXT and note each half, bounded depth (the halves' notes fold into
            the tree like any others)."""
            halves = split_text(node.text, max(estimate_tokens(node.text) // 2, 1))
            if len(halves) < 2:
                raise ItemError("a chunk kept overflowing and could not shrink further")
            notes: list[_ReductionNode] = []
            for half in halves:
                request = build_reduce_intermediate(goal, [half])
                try:
                    async with semaphore:
                        notes.append(
                            _ReductionNode(
                                text=await self.model.complete(request),
                                sources=node.sources,
                            )
                        )
                except ItemError as exc:
                    if depth <= 1 or not is_context_overflow(str(exc)):
                        raise
                    notes.extend(
                        await reduce_split(
                            _ReductionNode(text=half, sources=node.sources), depth - 1
                        )
                    )
            return notes

        async def reduce_chunk(chunk: tuple[int, ...]) -> list[_ReductionNode]:
            sources = frozenset(source for index in chunk for source in texts[index].sources)
            request = build_reduce_intermediate(goal, [texts[i].text for i in chunk])
            try:
                async with semaphore:  # released before any bisection recursion
                    return [
                        _ReductionNode(
                            text=await self.model.complete(request),
                            sources=sources,
                        )
                    ]
            except ItemError as exc:
                if is_context_overflow(str(exc)) and len(chunk) > 1:
                    # D26: the wire said this chunk is too big — the estimate
                    # lied, so split at item boundaries and retry both halves
                    self._note_bisection()
                    first, second = halve(chunk)
                    return [*await reduce_chunk(first), *await reduce_chunk(second)]
                if is_context_overflow(str(exc)) and len(chunk) == 1:
                    self._note_bisection()
                    try:
                        return await reduce_split(texts[chunk[0]], MAX_BISECT_DEPTH)
                    except ItemError as still:
                        exc = still  # fall through to the ordinary skip
                assert chunk, "chunk_indices never yields an empty chunk"
                diagnostics.warn(
                    f"skipped: chunk over items {chunk[0] + 1}-{chunk[-1] + 1} ({exc})"
                )
                self.skipped += 1
                lost.update(sources)
                if not isinstance(exc, UnsentError):
                    failed.update(sources)
                return []

        tasks = [asyncio.create_task(reduce_chunk(chunk)) for chunk in chunks]
        try:
            levels = await asyncio.gather(*tasks)
        except BaseException:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
            raise
        return [note for level in levels for note in level]

    async def _widen_if_possible(self, texts: list[_ReductionNode]) -> None:
        """D26 layer 1: the table budget looks too small — ask the provider for
        the real window, once. A bigger true window means fewer (or no) levels."""
        if self.window_budget is None or self.probed:
            return
        if fits_in_one([estimate_tokens(node.text) for node in texts], self.budget):
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
        from smartpipe.verbs.common import note_ambiguous_temporal

        request = build_reduce_final(instruction, texts, schema)
        reply = await self.model.complete(request)
        if schema is None:
            return reply.strip()
        try:
            return validate_and_coerce(reply, schema, note=note_ambiguous_temporal)
        except ItemError as first_error:
            repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
            return validate_and_coerce(
                await self.model.complete(repair), schema, note=note_ambiguous_temporal
            )


def _trace_line(trace: list[int]) -> str:
    tail = "".join(f" → {count}" for count in trace[2:])
    return f"reduce: {trace[0]:,} items → {trace[1]} chunks{tail}"
