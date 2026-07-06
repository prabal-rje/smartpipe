"""The ``split`` verb (D26 layer 3): oversized items → budget-sized chunk items.

Zero model calls. One 300-page PDF becomes N records of ``{"text", "source"}``
with provenance (``report.pdf §3/12``), each small enough for whatever verb
comes next. The taught pipeline: ``sempipe split --in big.pdf | sempipe map … |
sempipe reduce …``. Chunks concatenate back to the original text exactly.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, ItemError, UsageFault
from sempipe.engine.chunking import split_text
from sempipe.engine.units import SplitBy, parse_by
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.models.base import AudioData
from sempipe.verbs.common import ensure_text, interrupted_exit_code, outcome_exit_code

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import OutputFormat, ResultWriter

__all__ = ["SplitContext", "SplitRequest", "run_split"]

_DEFAULT_BUDGET_TOKENS = 2_000  # comfortable for every wired window, ~8k chars


@dataclass(frozen=True, slots=True)
class SplitRequest:
    max_tokens_flag: int | None = None
    by_flag: str | None = None  # --by UNIT[:N] (D26 rich units)
    input: InputSpec = STDIN


class SplitContext(Protocol):
    """The slice of the container ``split`` needs (no model — just the writer)."""

    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter: ...


def _resolve_by(request: SplitRequest) -> SplitBy:
    if request.by_flag is not None and request.max_tokens_flag is not None:
        raise UsageFault("--by and --max-tokens both set the unit — use one")
    if request.by_flag is not None:
        return parse_by(request.by_flag)
    if request.max_tokens_flag is not None:
        if request.max_tokens_flag < 1:
            raise UsageFault("--max-tokens must be at least 1")
        return SplitBy("tokens", request.max_tokens_flag)
    return SplitBy("tokens", _DEFAULT_BUDGET_TOKENS)


def _write_chunks(writer: ResultWriter, item: Item, by: SplitBy) -> None:
    origin = describe_source(item.source)  # "report.pdf" / "line 12"
    if by.unit in ("minutes", "seconds") and isinstance(item.media, AudioData):
        import base64

        from sempipe.parsing.extract import slice_audio

        step = by.slice_seconds
        slices = slice_audio(item.media, seconds=step)
        total = len(slices)
        for position, part in enumerate(slices):
            marker = (
                origin
                if total == 1
                else f"{origin} §{_clock(position * step)}-{_clock((position + 1) * step)}"
            )
            # audio rides NDJSON as base64 so the next verb can HEAR the slice
            writer.write_record(
                {
                    "audio_b64": base64.b64encode(part.data).decode("ascii"),
                    "mime": part.mime,
                    "source": marker,
                }
            )
        return
    chunks = split_text(item.text, by.amount if by.unit == "tokens" else _DEFAULT_BUDGET_TOKENS)
    total = len(chunks)
    for position, chunk in enumerate(chunks, start=1):
        marker = origin if total == 1 else f"{origin} §{position}/{total}"
        writer.write_record({"text": chunk, "source": marker})


def _clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


async def _run_pages(
    request: SplitRequest, context: SplitContext, *, by: SplitBy, stdout: TextIO
) -> ExitCode:
    """--by pages reads PDF FILES directly (page structure dies in extraction)."""
    from pathlib import Path

    from sempipe.io.inputs import expand_globs
    from sempipe.io.writers import OutputFormat
    from sempipe.parsing.extract import pdf_page_texts

    if not request.input.patterns:
        raise UsageFault(
            "--by pages reads PDF files — give it some: sempipe split --by pages --in 'docs/*.pdf'"
        )
    writer = context.writer(OutputFormat.AUTO, structured=True, stdout=stdout)
    produced = 0
    skipped = 0
    try:
        for path in expand_globs(request.input.patterns):
            name = Path(path).name
            if Path(path).suffix.lower() != ".pdf":
                diagnostics.warn(
                    f"skipped: {name} (--by pages reads PDF files — "
                    f"{name} has no fixed pages; use --by tokens)"
                )
                skipped += 1
                continue
            try:
                pages = pdf_page_texts(Path(path))
            except ItemError as exc:
                diagnostics.warn(f"skipped: {name} ({exc})")
                skipped += 1
                continue
            groups = [pages[i : i + by.amount] for i in range(0, len(pages), by.amount)]
            for index, group in enumerate(groups):
                first = index * by.amount + 1
                last = min(first + by.amount - 1, len(pages))
                span = f"p.{first}" if first == last else f"p.{first}-{last}"
                marker = name if len(groups) == 1 else f"{name} {span}"
                writer.write_record({"text": "\n\n".join(group).strip(), "source": marker})
            produced += 1
    finally:
        writer.flush()
    return outcome_exit_code(done=produced, skipped=skipped)


async def run_split(
    request: SplitRequest,
    context: SplitContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    from sempipe.io.writers import OutputFormat

    by = _resolve_by(request)
    if by.unit == "pages":
        return await _run_pages(request, context, by=by, stdout=stdout)
    writer = context.writer(OutputFormat.AUTO, structured=True, stdout=stdout)
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
    produced = 0
    skipped = 0
    try:
        async for item in items_iter:
            if stop is not None and stop.is_set():
                break
            duration_slicing = by.unit in ("minutes", "seconds")
            if not (duration_slicing and isinstance(item.media, AudioData)):
                try:
                    item = await ensure_text(item)  # audio transcribes; images skip
                except ItemError as exc:
                    diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
                    skipped += 1
                    continue
            try:
                _write_chunks(writer, item, by)
            except ItemError as exc:
                diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
                skipped += 1
                continue
            produced += 1
    finally:
        writer.flush()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=produced, skipped=skipped)
        return interrupted_exit_code(done=produced, skipped=skipped)
    return outcome_exit_code(done=produced, skipped=skipped)
