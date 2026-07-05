"""The ``embed`` verb: turn each item into a vector (spec §3.3).

The only verb that never touches a chat LLM — it uses the embedding model and
emits one NDJSON record per item: ``{"text", "vector", "source"}``. Output is
always NDJSON (a vector has no human view), so it feeds ``top_k`` or a file.

Two execution shapes (plan/post-1.0/06, DEFER-3): a finite file corpus is
embedded in ≤64-text chunks (64x fewer round-trips, poison chunks re-run
item-by-item); a stream stays one item per call — latency beats throughput
when lines arrive over time.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode
from sempipe.engine.runner import Done, FailurePolicy, run_ordered
from sempipe.io import diagnostics, readers, tty
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.progress import make_stderr_spinner
from sempipe.io.writers import RenderMode, WriterConfig, make_writer
from sempipe.verbs.common import (
    embed_in_batches,
    ensure_text_item,
    interrupted_exit_code,
    outcome_exit_code,
)

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.models.base import EmbeddingModel

__all__ = ["EmbedContext", "EmbedRequest", "run_embed"]


@dataclass(frozen=True, slots=True)
class EmbedRequest:
    model_flag: str | None
    concurrency_flag: int | None
    input: InputSpec = STDIN
    fields: tuple[str, ...] | None = None  # --fields: project the {text, vector, source} records


class EmbedContext(Protocol):
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...


async def run_embed(
    request: EmbedRequest,
    context: EmbedContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    model = await context.embedding_model(request.model_flag)
    concurrency = context.concurrency(request.concurrency_flag)

    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    if (total is None or total > 0) and tty.stdout_is_tty():
        diagnostics.note(
            "embeddings are large — redirect to a file: sempipe embed > corpus.embeddings"
        )
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=request.fields), stdout
    )
    spinner = make_stderr_spinner()
    spinner.start(total=total)

    done = 0
    skipped = 0
    if total is not None:
        # finite --in corpus: chunked calls, run_ordered bypassed on purpose —
        # batching ≠ per-item workers (order from sequential chunks, isolation
        # from the per-item fallback inside embed_in_batches)
        collected = [item async for item in items_iter]
        outcomes = embed_in_batches(model, collected, failure_policy=FailurePolicy(), stop=stop)
    else:
        # live stream: one item per call — latency beats throughput

        async def worker(item: Item) -> tuple[Item, tuple[float, ...]]:
            return item, await _embed_one(model, item)

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
                item, vector = outcome.value
                writer.write_record(
                    {
                        "text": item.text,
                        "vector": list(vector),
                        "source": item.source.name,
                    }
                )
                done += 1
            else:  # Skipped
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


async def _embed_one(model: EmbeddingModel, item: Item) -> tuple[float, ...]:
    ensure_text_item(item)  # image items need map — ItemError → skip-and-warn
    vectors = await model.embed([item.text])
    return vectors[0]
