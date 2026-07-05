"""The ``top_k`` verb: rank items by similarity to a query (spec §3.4).

Embeds the query and every item (reusing a precomputed ``vector`` field from an
``embed`` record when present), ranks by cosine, and keeps the top K and/or
everything above a threshold — reordered, each with a ``_score``. Unlike the
per-item verbs, ``top_k`` inherently buffers: it must see all scores to rank.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, SetupFault, UsageFault
from sempipe.core.jsontools import as_float_vector
from sempipe.engine.ranking import rank, select
from sempipe.engine.runner import Done, FailurePolicy, run_ordered
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.progress import make_stderr_spinner
from sempipe.io.writers import RenderMode, WriterConfig, make_writer
from sempipe.verbs.common import aiter_items

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import ResultWriter
    from sempipe.models.base import EmbeddingModel

__all__ = ["TopKContext", "TopKRequest", "run_top_k"]


@dataclass(frozen=True, slots=True)
class TopKRequest:
    near: str
    k: int | None
    threshold: float | None
    model_flag: str | None
    concurrency_flag: int | None
    input: InputSpec = STDIN


class TopKContext(Protocol):
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...


async def run_top_k(
    request: TopKRequest, context: TopKContext, *, stdin: TextIO, stdout: TextIO
) -> ExitCode:
    if request.k is None and request.threshold is None:
        raise UsageFault("top_k needs a number (K), --threshold, or both")
    model = await context.embedding_model(request.model_flag)
    concurrency = context.concurrency(request.concurrency_flag)

    items = [item async for item in readers.resolve_items(request.input, stdin)]
    if not items:
        return ExitCode.OK

    query_vector = (await model.embed([request.near]))[0]
    vectors, skipped = await _collect_vectors(model, items, concurrency)
    _check_dimensions(query_vector, vectors)

    entries = sorted(vectors.items())  # (item_index, vector), stable by index for ties
    ranked = rank(query_vector, [vector for _, vector in entries])
    scored = tuple((entries[position][0], score) for position, score in ranked)
    chosen = select(scored, k=request.k, threshold=request.threshold)

    by_index = {item.source.index: item for item in items}
    writer = make_writer(WriterConfig(mode=RenderMode.TEXT, color=False, width=80), stdout)
    for item_index, score in chosen:
        _emit(writer, by_index[item_index], score)
    writer.flush()

    if skipped == 0:
        return ExitCode.OK
    return ExitCode.ALL_FAILED if not vectors else ExitCode.PARTIAL


async def _collect_vectors(
    model: EmbeddingModel, items: list[Item], concurrency: int
) -> tuple[dict[int, tuple[float, ...]], int]:
    vectors: dict[int, tuple[float, ...]] = {}
    to_embed: list[Item] = []
    for item in items:
        precomputed = _precomputed_vector(item)
        if precomputed is not None:
            vectors[item.source.index] = precomputed
        else:
            to_embed.append(item)

    spinner = make_stderr_spinner()
    spinner.start(total=len(to_embed))
    skipped = 0

    async def worker(item: Item) -> tuple[float, ...]:
        return (await model.embed([item.text]))[0]

    outcomes = run_ordered(
        aiter_items(to_embed), worker, concurrency=concurrency, failure_policy=FailurePolicy()
    )
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                vectors[outcome.index] = outcome.value
            else:  # Skipped
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
            spinner.advance()
    finally:
        spinner.finish()
    return vectors, skipped


def _precomputed_vector(item: Item) -> tuple[float, ...] | None:
    """An ``embed`` record carries its own ``vector`` — skip re-embedding it (spec §3.4)."""
    if item.data is None:
        return None
    return as_float_vector(item.data.get("vector"))


def _check_dimensions(query: tuple[float, ...], vectors: dict[int, tuple[float, ...]]) -> None:
    for vector in vectors.values():
        if len(vector) != len(query):
            raise SetupFault(
                f"error: the corpus and the query were embedded with different models "
                f"(dimensions {len(vector)} vs {len(query)})\n"
                "  Use the same embedding model for both — e.g. re-run embed and top_k\n"
                "  with the same --embed-model, or check SEMPIPE_EMBED_MODEL."
            )


def _emit(writer: ResultWriter, item: Item, score: float) -> None:
    rounded = round(score, 4)
    if item.source.kind == "file":  # rank files → get filenames back (the resume demo)
        writer.write_text(f"{item.source.name}\t{rounded}")
    elif item.data is not None:
        record = {key: value for key, value in item.data.items() if key != "vector"}
        record["_score"] = rounded
        writer.write_record(record)
    else:
        writer.write_text(f"{item.raw}\t{rounded}")
