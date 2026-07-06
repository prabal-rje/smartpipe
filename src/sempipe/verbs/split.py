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
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.verbs.common import ensure_text, interrupted_exit_code, outcome_exit_code

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.inputs import InputSpec
    from sempipe.io.writers import OutputFormat, ResultWriter

__all__ = ["SplitContext", "SplitRequest", "run_split"]

_DEFAULT_BUDGET_TOKENS = 2_000  # comfortable for every wired window, ~8k chars


@dataclass(frozen=True, slots=True)
class SplitRequest:
    max_tokens_flag: int | None = None
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


async def run_split(
    request: SplitRequest,
    context: SplitContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    from sempipe.io.writers import OutputFormat

    budget = (
        request.max_tokens_flag if request.max_tokens_flag is not None else (_DEFAULT_BUDGET_TOKENS)
    )
    if budget < 1:
        raise UsageFault("--max-tokens must be at least 1")
    writer = context.writer(OutputFormat.AUTO, structured=True, stdout=stdout)
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
    produced = 0
    skipped = 0
    try:
        async for item in items_iter:
            if stop is not None and stop.is_set():
                break
            try:
                item = await ensure_text(item)  # audio transcribes; images skip
            except ItemError as exc:
                diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
                skipped += 1
                continue
            chunks = split_text(item.text, budget)
            total = len(chunks)
            origin = describe_source(item.source)  # "report.pdf" / "line 12"
            for position, chunk in enumerate(chunks, start=1):
                marker = origin if total == 1 else f"{origin} §{position}/{total}"
                writer.write_record({"text": chunk, "source": marker})
            produced += 1
    finally:
        writer.flush()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=produced, skipped=skipped)
        return interrupted_exit_code(done=produced, skipped=skipped)
    return outcome_exit_code(done=produced, skipped=skipped)
