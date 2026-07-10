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
from smartpipe.engine.runner import Done, FailurePolicy
from smartpipe.io import diagnostics, readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
from smartpipe.verbs.common import embed_in_batches
from smartpipe.verbs.convert import make_converter
from smartpipe.verbs.distinct import DistinctContext
from smartpipe.verbs.embed import optional_chat

if TYPE_CHECKING:
    from typing import TextIO

    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item

__all__ = ["OutliersRequest", "run_outliers"]

_NEIGHBORS = 5  # kNN depth — internal, not a knob


@dataclass(frozen=True, slots=True)
class OutliersRequest:
    count: int = 5
    model_flag: str | None = None
    concurrency_flag: int | None = None
    allow_captions: bool = False
    input: InputSpec = STDIN


async def run_outliers(
    request: OutliersRequest,
    context: DistinctContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    if request.count < 1:
        raise UsageFault("outliers needs a positive count")
    model = await context.embedding_model(request.model_flag)
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
    items = [item async for item in items_iter]
    if len(items) < 3:
        raise UsageFault("outliers needs at least 3 items to know what normal looks like")

    log = diagnostics.DegradationLog()
    converter_chat = await optional_chat(context)
    converter = make_converter(
        converter_chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(converter_chat.ref if converter_chat else None),
    )
    scored_items: list[Item] = []
    vectors: list[tuple[float, ...]] = []
    position = 0
    outcomes = embed_in_batches(
        model,
        items,
        failure_policy=FailurePolicy(),
        stop=stop,
        log=log,
        converter=converter,
    )
    async for outcome in outcomes:
        if isinstance(outcome, Done):
            embedded, vector = outcome.value
            scored_items.append(embedded)
            vectors.append(vector)
        else:  # an unexamined item can't be scored — excluded, disclosed
            diagnostics.warn(f"excluded: {describe_source(outcome.source)} ({outcome.reason})")
        position += 1
    log.finish()
    if len(vectors) < 3:
        raise UsageFault("outliers needs at least 3 embeddable items")

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
    return ExitCode.OK
