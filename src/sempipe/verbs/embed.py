"""The ``embed`` verb: turn each item into a vector (spec §3.3).

The only verb that never touches a chat LLM — it uses the embedding model and
emits one NDJSON record per item: ``{"text", "vector", "source"}``. Output is
always NDJSON (a vector has no human view), so it feeds ``top_k`` or a file.

Embeddings are requested one item per call through the ordered runner, which keeps
failure isolation per-item; batching the embed endpoint is a future optimization
recorded in the plan ledger.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode
from sempipe.engine.runner import Done, FailurePolicy, run_ordered
from sempipe.io import diagnostics, readers, tty
from sempipe.io.items import describe_source
from sempipe.io.progress import make_stderr_spinner
from sempipe.io.writers import RenderMode, WriterConfig, make_writer
from sempipe.verbs.common import aiter_items, outcome_exit_code

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.items import Item
    from sempipe.models.base import EmbeddingModel

__all__ = ["EmbedContext", "EmbedRequest", "run_embed"]


@dataclass(frozen=True, slots=True)
class EmbedRequest:
    model_flag: str | None
    concurrency_flag: int | None


class EmbedContext(Protocol):
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...


async def run_embed(
    request: EmbedRequest, context: EmbedContext, *, stdin: TextIO, stdout: TextIO
) -> ExitCode:
    readers.ensure_not_a_tty(stdin)
    model = await context.embedding_model(request.model_flag)
    concurrency = context.concurrency(request.concurrency_flag)

    items = [item async for item in readers.stdin_items(stdin)]
    if items and tty.stdout_is_tty():
        diagnostics.note(
            "embeddings are large — redirect to a file: sempipe embed > corpus.embeddings"
        )
    writer = make_writer(WriterConfig(mode=RenderMode.NDJSON, color=False, width=80), stdout)
    spinner = make_stderr_spinner()
    spinner.start(total=len(items))
    by_index = {item.source.index: item for item in items}

    async def worker(item: Item) -> tuple[float, ...]:
        return await _embed_one(model, item)

    done = 0
    skipped = 0
    outcomes = run_ordered(
        aiter_items(items), worker, concurrency=concurrency, failure_policy=FailurePolicy()
    )
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                item = by_index[outcome.index]
                writer.write_record(
                    {
                        "text": item.text,
                        "vector": list(outcome.value),
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
    return outcome_exit_code(done=done, skipped=skipped)


async def _embed_one(model: EmbeddingModel, item: Item) -> tuple[float, ...]:
    vectors = await model.embed([item.text])
    return vectors[0]
