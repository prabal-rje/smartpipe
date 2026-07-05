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
from sempipe.engine.chunking import budget_for, chunk_indices, estimate_tokens, fits_in_one
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
from sempipe.io import diagnostics, readers
from sempipe.io.items import describe_source
from sempipe.io.writers import OutputFormat

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path
    from typing import TextIO

    from sempipe.engine.prompts import Token
    from sempipe.io.items import Item
    from sempipe.io.writers import ResultWriter
    from sempipe.models.base import ChatModel

__all__ = ["ReduceContext", "ReduceRequest", "run_reduce"]

_OVERHEAD_TOKENS = 300  # reserve room for the prompt template + response


@dataclass(frozen=True, slots=True)
class ReduceRequest:
    prompt: str
    schema_path: Path | None
    group_by: str | None
    model_flag: str | None
    concurrency_flag: int | None
    verbose: bool


class ReduceContext(Protocol):
    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...
    def writer(
        self, output_flag: OutputFormat, *, structured: bool, stdout: TextIO
    ) -> ResultWriter: ...


async def run_reduce(
    request: ReduceRequest, context: ReduceContext, *, stdin: TextIO, stdout: TextIO
) -> ExitCode:
    readers.ensure_not_a_tty(stdin)
    tokens = parse_prompt(request.prompt)
    reject_comma_groups(tokens)
    if has_brace(tokens) and request.group_by is None:
        raise UsageFault(
            "field references like {x} in reduce only work with --group-by "
            "(where {field} means the group's value)"
        )
    schema = load_schema(request.schema_path) if request.schema_path is not None else None
    model = await context.chat_model(request.model_flag)
    concurrency = context.concurrency(request.concurrency_flag)
    structured = schema is not None
    writer = context.writer(OutputFormat.AUTO, structured=structured, stdout=stdout)

    items = [item async for item in readers.stdin_items(stdin)]
    if not items:
        return ExitCode.OK

    reducer = _Reducer(
        model=model,
        budget=budget_for(model.ref.provider, prompt_overhead=_OVERHEAD_TOKENS),
        concurrency=concurrency,
        verbose=request.verbose,
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
    return ExitCode.PARTIAL if reducer.skipped else ExitCode.OK


async def _run_single(
    reducer: _Reducer,
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


async def _run_grouped(
    reducer: _Reducer,
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


def _group(
    items: list[Item], field_name: str, reducer: _Reducer
) -> list[tuple[object, list[Item]]]:
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


@dataclass
class _Reducer:
    model: ChatModel
    budget: int
    concurrency: int
    verbose: bool
    skipped: int = 0
    produced: int = 0

    async def reduce(
        self, instruction: str, schema: Mapping[str, object] | None, texts: Sequence[str]
    ) -> str | Mapping[str, object]:
        trace = [len(texts)]
        current = list(texts)
        semaphore = asyncio.Semaphore(self.concurrency)
        while not fits_in_one([estimate_tokens(t) for t in current], self.budget):
            chunks = chunk_indices([estimate_tokens(t) for t in current], self.budget)
            trace.append(len(chunks))
            current = await self._reduce_level(instruction, current, chunks, semaphore)
            if not current:
                raise ItemError("every chunk failed to reduce")
        if len(trace) > 1:
            trace.append(1)
            if self.verbose:
                diagnostics.note(_trace_line(trace))
        return await self._final(instruction, schema, current)

    async def _reduce_level(
        self,
        goal: str,
        texts: list[str],
        chunks: tuple[tuple[int, ...], ...],
        semaphore: asyncio.Semaphore,
    ) -> list[str]:
        async def reduce_chunk(chunk: tuple[int, ...]) -> str | None:
            async with semaphore:
                request = build_reduce_intermediate(goal, [texts[i] for i in chunk])
                try:
                    return await self.model.complete(request)
                except ItemError as exc:
                    diagnostics.warn(
                        f"skipped: chunk over items {chunk[0] + 1}-{chunk[-1] + 1} ({exc})"
                    )
                    self.skipped += 1
                    return None

        tasks = [asyncio.create_task(reduce_chunk(chunk)) for chunk in chunks]
        notes = [await task for task in tasks]  # awaited in order
        return [note for note in notes if note is not None]

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
