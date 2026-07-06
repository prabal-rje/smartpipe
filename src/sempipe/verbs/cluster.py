"""The ``cluster`` verb: group by meaning, label each group (D38/05).

KQL's ``autocluster``/``reduce by`` done semantically: N embeddings + one
label call per cluster — never N chat calls. The Monday slide (P3), the
phishing lure families (P6), and the qualitative codebook (P10) as one verb.
"""

from __future__ import annotations

import asyncio
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, ItemError, UsageFault
from sempipe.engine.chunking import mean_pool
from sempipe.engine.clustering import adaptive_threshold, leader_clusters, merge_to_k
from sempipe.engine.ranking import cosine
from sempipe.engine.runner import Done, FailurePolicy
from sempipe.engine.schema import validate_and_coerce
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import describe_source
from sempipe.io.writers import RenderMode, WriterConfig, make_writer
from sempipe.models.base import ChatModel, CompletionRequest
from sempipe.verbs.common import embed_in_batches
from sempipe.verbs.convert import make_converter
from sempipe.verbs.embed import optional_chat

if TYPE_CHECKING:
    from typing import TextIO

    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.models.base import EmbeddingModel

__all__ = ["ClusterRequest", "run_cluster"]

_EXAMPLES = 3

_LABEL_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "label": {"type": "string", "description": "3-6 words naming what unites the items"}
    },
    "required": ["label"],
    "additionalProperties": False,
}

_LABEL_SYSTEM = (
    "You name clusters of similar items. Reply with a short, specific label "
    "(3-6 words) capturing what unites them — content, not form."
)


@dataclass(frozen=True, slots=True)
class ClusterRequest:
    k: int | None = None
    top: int | None = None
    explode: str | None = None  # "members" is the only value
    model_flag: str | None = None  # chat, for labels
    embed_flag: str | None = None
    concurrency_flag: int | None = None
    allow_captions: bool = False
    input: InputSpec = STDIN


class ClusterContext(Protocol):
    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...


async def run_cluster(
    request: ClusterRequest,
    context: ClusterContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    if request.explode is not None and request.explode != "members":
        raise UsageFault("--explode takes exactly 'members' (one row per input item)")
    if request.k is not None and request.k < 1:
        raise UsageFault("--k needs a positive cluster count")
    model = await context.embedding_model(request.embed_flag)
    items_iter, _total = readers.resolve_items(request.input, stdin, stop=stop)
    items = [item async for item in items_iter]
    if not items:
        return ExitCode.OK
    diagnostics.note(
        f"cluster: ~{len(items):,} embeddings + one label call per cluster (typically < 20)"
    )

    log = diagnostics.DegradationLog()
    converter = make_converter(
        await optional_chat(context), allow_paid=request.allow_captions, log=log
    )
    clustered_items: list[Item] = []
    vectors: list[tuple[float, ...]] = []
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
            clustered_items.append(embedded)
            vectors.append(vector)
        else:
            diagnostics.warn(f"excluded: {describe_source(outcome.source)} ({outcome.reason})")
    log.finish()
    if not vectors:
        return ExitCode.ALL_FAILED

    # the threshold adapts to the embedder's geometry (measured: gemini's
    # same-theme pairs sit near 0.7; a fixed bar can't serve every model)
    clusters = leader_clusters(vectors, threshold=adaptive_threshold(vectors))
    if request.k is not None and len(clusters) > request.k:
        clusters = merge_to_k(vectors, clusters, k=request.k)
    clusters = sorted(clusters, key=len, reverse=True)

    labels = await _label_clusters(context, request, clusters, clustered_items)

    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=None), stdout
    )
    if request.explode == "members":
        member_label: dict[int, str] = {}
        for members, label in zip(clusters, labels, strict=True):
            for member in members:
                member_label[member] = label
        for position, item in enumerate(clustered_items):
            record: dict[str, object]
            if item.data is not None:
                record = {key: value for key, value in item.data.items() if key != "vector"}
            else:
                record = {"text": item.raw}
            record["cluster"] = member_label[position]
            writer.write_record(record)
        writer.flush()
        return ExitCode.OK

    total = len(clustered_items)
    shown = clusters if request.top is None else clusters[: request.top]
    for members, label in zip(shown, labels[: len(shown)], strict=True):
        writer.write_record(
            {
                "cluster": label,
                "size": len(members),
                "share": round(len(members) / total, 2),
                "examples": _examples(members, clustered_items, vectors),
            }
        )
    folded = clusters[len(shown) :]
    if folded:
        size = sum(len(members) for members in folded)
        writer.write_record(
            {"cluster": "(other)", "size": size, "share": round(size / total, 2), "examples": []}
        )
    writer.flush()
    return ExitCode.OK


async def _label_clusters(
    context: ClusterContext,
    request: ClusterRequest,
    clusters: list[list[int]],
    items: list[Item],
) -> list[str]:
    if request.model_flag is not None:
        chat: ChatModel | None = await context.chat_model(request.model_flag)
    else:
        chat = await optional_chat(context)
    if chat is None:
        diagnostics.note(
            "no chat model — clusters are unnamed (cluster 1, 2, …); "
            "configure one for real labels: sempipe config"
        )
        return [f"cluster {number}" for number in range(1, len(clusters) + 1)]
    labels: list[str] = []
    for number, members in enumerate(clusters, start=1):
        quotes = "\n".join(f"- {items[member].text[:200]}" for member in members[:8])
        try:
            reply = await chat.complete(
                CompletionRequest(
                    system=_LABEL_SYSTEM,
                    user=f"Items in this cluster:\n{quotes}",
                    json_schema=_LABEL_SCHEMA,
                    max_tokens=64,
                )
            )
            verdict = validate_and_coerce(reply, _LABEL_SCHEMA)
            label = str(verdict["label"]).strip() or f"cluster {number}"
        except ItemError as exc:
            diagnostics.warn(f"cluster {number} label failed ({exc}) — kept numbered")
            label = f"cluster {number}"
        labels.append(label)
    return labels


def _examples(members: list[int], items: list[Item], vectors: list[tuple[float, ...]]) -> list[str]:
    centroid = mean_pool([vectors[member] for member in members])
    nearest = sorted(members, key=lambda member: -cosine(vectors[member], centroid))
    return [items[member].text[:160] for member in nearest[:_EXAMPLES]]
