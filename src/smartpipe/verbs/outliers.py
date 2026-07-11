"""The ``outliers`` verb: the items least like the rest (D38/04).

top_k's mirror — "farthest from everything" instead of "nearest to the
query". Embeddings only; the weirdness score is mean cosine distance to the
k nearest neighbors, which stays honest on multi-cluster corpora.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.engine.clustering import knn_mean_distance
from smartpipe.engine.runner import Done
from smartpipe.io import diagnostics, readers, source_accounting
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
from smartpipe.verbs.common import embed_in_batches, outcome_exit_code
from smartpipe.verbs.convert import make_converter
from smartpipe.verbs.distinct import DistinctContext
from smartpipe.verbs.embed import optional_chat

if TYPE_CHECKING:
    from typing import TextIO

    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.models.budget import CallBudget

__all__ = ["OutliersRequest", "run_outliers"]

_NEIGHBORS = 5  # kNN depth — internal, not a knob


@dataclass(frozen=True, slots=True)
class OutliersRequest:
    count: int = 5
    model_flag: str | None = None
    concurrency_flag: int | None = None
    allow_captions: bool = False
    input: InputSpec = STDIN
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion (item 48)


async def run_outliers(
    request: OutliersRequest,
    context: DistinctContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
    budget: CallBudget | None = None,
) -> ExitCode:
    if request.count < 1:
        raise UsageFault("outliers needs a positive count")
    concurrency = context.concurrency(request.concurrency_flag)
    model = await context.embedding_model(request.model_flag)
    failure_policy = context.failure_policy(model.ref.provider)
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    items_iter, _total = readers.resolve_items(
        request.input, stdin, stop=stop, ocr=ocr, budget=budget
    )
    items = [item async for item in items_iter]
    if not items:
        # A8 review: a belt-shortfall decline (or a genuinely empty corpus) reads
        # nothing - exit 0 having spent zero, like cluster/distinct/top_k. A "needs
        # at least 3 items" usage fault here would mislabel a run the user declined.
        return outcome_exit_code(done=0, skipped=0, failed=0)
    if len(items) < 3:
        raise UsageFault("outliers needs at least 3 items to know what normal looks like")

    converter_chat = await optional_chat(context)
    converter = make_converter(
        converter_chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(converter_chat.ref if converter_chat else None),
        ocr=ocr,
    )
    scored_items: list[Item] = []
    vectors: list[tuple[float, ...]] = []
    embedding_skipped = 0
    embedding_failed = 0
    sources = source_accounting.SourceCounter()
    outcomes = embed_in_batches(
        model,
        items,
        failure_policy=failure_policy,
        call_concurrency=concurrency,
        stop=stop,
        log=log,
        converter=converter,
    )
    async for outcome in outcomes:
        if isinstance(outcome, Done):
            embedded, vector = outcome.value
            scored_items.append(embedded)
            vectors.append(vector)
            sources.done(embedded.source)
        else:  # an unexamined item can't be scored — excluded, disclosed
            diagnostics.warn(f"excluded: {describe_source(outcome.source)} ({outcome.reason})")
            embedding_skipped += 1
            embedding_failed += int(outcome.failed)
            sources.skip(outcome.source, failed=outcome.failed)
    log.finish()
    if len(vectors) < 3:
        diagnostics.warn("outliers: fewer than 3 items embedded - no ranking can be formed")
        return outcome_exit_code(
            done=0,
            skipped=len(items),
            failed=embedding_failed,
            source_counts=sources.counts,
        )

    distances = knn_mean_distance(vectors, k=_NEIGHBORS)
    ranked = sorted(range(len(distances)), key=lambda index: -distances[index])
    top = ranked[: request.count]
    median = sorted(distances)[len(distances) // 2]
    if median > 0:
        low = distances[top[-1]] / median
        high = distances[top[0]] / median
        diagnostics.note(
            f"outliers: median neighbor distance {median:.2f} — these are "
            f"{low:.1f}x-{high:.1f}x out"
        )

    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=None), stdout
    )
    for index in top:
        item = scored_items[index]
        record: dict[str, object]
        if item.data is not None:
            record = {key: value for key, value in item.data.items() if key != "vector"}
        else:
            record = {"text": item.raw}
        record["__distance"] = round(distances[index], 4)
        record.setdefault("source", describe_source(item.source))
        writer.write_record(record)
    writer.flush()
    return outcome_exit_code(
        done=len(vectors),
        skipped=embedding_skipped,
        failed=embedding_failed,
        source_counts=sources.counts,
    )
