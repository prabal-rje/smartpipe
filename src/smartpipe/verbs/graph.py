"""The ``graph`` verb, ``--fast`` half (wave G1): entities + co-occurrence, free.

The cost shape IS the feature: a local NER model finds user-named entities, a
local embedder folds near-duplicate names, and co-occurrence inside the
``--window`` dial becomes weighted edges with spine-ref provenance — zero chat
calls, nothing leaves the machine. stdout is JSONL edges; ``--save`` writes
graphml/dot/mermaid/csv/html or an Obsidian vault. The paid modes are a later
wave; this one must stay free by construction, and the tests pin that.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING, Protocol, assert_never

from smartpipe.core.errors import ExitCode, ItemError, UsageFault
from smartpipe.engine.graphkg import (
    ItemEntities,
    SpineRef,
    build_nodes,
    fold_edges,
    fold_stats,
    fold_surfaces,
    parse_window,
    prune_edges,
    spine_record,
    surface_counts,
    windows,
)
from smartpipe.engine.graphout import (
    MERMAID_DEFAULT_CAP,
    SaveFormat,
    save_format,
    to_dot,
    to_edges_csv,
    to_graphml,
    to_html,
    to_mermaid,
    to_nodes_csv,
    to_obsidian,
)
from smartpipe.io import diagnostics, readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source, project_content
from smartpipe.io.progress import Spinner, make_stderr_spinner
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
from smartpipe.verbs.common import EMBED_BATCH_SIZE, batched, ensure_text, outcome_exit_code
from smartpipe.verbs.common import transcribe as whisper_transcribe

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from pathlib import Path
    from typing import TextIO

    from smartpipe.engine.graphkg import EntityFinder, GraphEdge, GraphNode
    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item, ItemSource
    from smartpipe.models.base import AudioData, EmbeddingModel

__all__ = ["DEFAULT_ENTITIES", "GraphRequest", "parse_entities", "run_graph"]

DEFAULT_ENTITIES = ("person", "organization", "location")

_PACE_SAMPLE = 20  # windows before this machine's pace is worth projecting
_PACE_NOTE_S = 120.0  # projected grinds past two minutes get one honest note


@dataclass(frozen=True, slots=True)
class GraphRequest:
    fast: bool = False
    entities: str | None = None  # comma-separated user-named types; None = the default set
    window: str = "chunk"
    min_weight: int = 1
    save: str | None = None
    top: int | None = None  # display-format hub cap
    input: InputSpec = STDIN


class GraphContext(Protocol):
    """What ``--fast`` needs — deliberately NO chat accessor: the free half
    cannot ask for a paid model even by accident."""

    def entity_finder(self, labels: Sequence[str]) -> EntityFinder: ...
    def fold_embedder(self) -> EmbeddingModel: ...


def parse_entities(raw: str | None) -> tuple[str, ...]:
    """The ``--entities "a, b"`` dial: trimmed, deduped, order kept."""
    if raw is None:
        return DEFAULT_ENTITIES
    labels = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not labels:
        raise UsageFault(
            '--entities needs at least one type\n  Example: --entities "person, vessel, account"'
        )
    return labels


async def run_graph(
    request: GraphRequest,
    context: GraphContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
    transcriber: Callable[[AudioData], str] = whisper_transcribe,
    clock: Callable[[], float] = time.monotonic,
) -> ExitCode:
    if not request.fast:
        raise UsageFault(
            "graph needs --fast for now — the free, on-device co-occurrence mode\n"
            "  --fast makes zero model calls; the model-read modes arrive in a later release."
        )
    if request.min_weight < 1:
        raise UsageFault("--min-weight needs a positive co-occurrence count")
    if request.top is not None and request.top < 1:
        raise UsageFault("--top needs a positive node count")
    labels = parse_entities(request.entities)
    mode = parse_window(request.window)
    if request.save is not None:
        save_format(request.save)  # a typo'd extension must refuse BEFORE the work

    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    read_bar = _stage("read")
    read_bar.start(total)
    items: list[Item] = []
    async for item in items_iter:
        items.append(item)
        read_bar.advance()
    read_bar.finish()
    if not items:
        return ExitCode.OK

    finder = context.entity_finder(labels)
    log = diagnostics.DegradationLog()
    gathered: list[ItemEntities] = []
    no_free_text = 0
    failed = 0
    entity_bar = _stage("entities")
    entity_bar.start(len(items))
    stage_start = clock()
    for position, item in enumerate(items, start=1):
        if stop is not None and stop.is_set():
            break
        try:
            # projection first (a no-op for media records), THEN the free ladder:
            # a transcript must never be re-projected away by the original record
            textual = await ensure_text(
                project_content(item), transcriber=transcriber, log=log, converter=None
            )
        except ItemError:
            # the free ladder has no rung for this item (image/scan) — censused below
            no_free_text += 1
            entity_bar.advance()
            continue
        text = textual.text
        try:
            spans = await asyncio.to_thread(finder.find, text)
        except ItemError as exc:
            failed += 1
            diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
            entity_bar.advance()
            continue
        gathered.append(
            ItemEntities(
                ref=_spine_ref(item.source),
                doc=item.source.path or item.source.name,
                text=text,
                spans=spans,
            )
        )
        entity_bar.advance()
        if position == _PACE_SAMPLE and len(items) > _PACE_SAMPLE:
            _note_projected_grind(clock() - stage_start, len(items))
    entity_bar.finish()
    log.finish()
    if no_free_text:
        plural = "s" if no_free_text != 1 else ""
        diagnostics.note(
            f"{no_free_text:,} file{plural} skipped — no free text (images/scans); "
            "the full mode or ocr-model reads them"
        )

    counts = surface_counts(gathered)
    vectors = await _fold_vectors(context, [surface.name for surface in counts])
    canonical = fold_surfaces(counts, vectors)
    folded_names, folded_nodes = fold_stats(canonical)
    if folded_names:
        plural = "s" if folded_nodes != 1 else ""
        diagnostics.note(f"{folded_names} entity names folded into {folded_nodes} node{plural}")

    nodes = build_nodes(counts, canonical)
    edges = fold_edges(windows(gathered, mode), canonical)
    kept, pruned = prune_edges(edges, request.min_weight)

    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=None), stdout
    )
    write_bar = _stage("write")
    write_bar.start(len(kept))
    for edge in kept:
        writer.write_record(
            {
                "source": edge.source,
                "relation": edge.relation,
                "target": edge.target,
                "weight": edge.weight,
                "sources": [spine_record(ref) for ref in edge.sources],
            }
        )
        write_bar.advance()
    writer.flush()
    write_bar.finish()

    if request.save is not None:
        _save(request.save, nodes, kept, top=request.top)

    diagnostics.note(
        f"graph: {len(counts):,} entities ({folded_names:,} folded) · "
        f"{len(kept):,} edges ({pruned:,} pruned) · 0 tok"
    )
    return outcome_exit_code(done=len(gathered), skipped=no_free_text + failed)


def _stage(name: str) -> Spinner:
    """The G0 bar wearing this stage's name (a ``run`` pipeline's label wins)."""
    spinner = make_stderr_spinner()
    if spinner.label is None:
        spinner.label = name
    return spinner


def _note_projected_grind(elapsed: float, total_windows: int) -> None:
    """Projected-time honesty (owner ruling): after the sample, this machine's
    measured pace projects the whole run — past ~2 minutes, say so once."""
    projected = elapsed / _PACE_SAMPLE * total_windows
    if projected <= _PACE_NOTE_S:
        return
    minutes = max(1, round(projected / 60))
    diagnostics.note(
        f"~{total_windows:,} windows at this machine's pace — roughly {minutes} min "
        "(progress below; Ctrl-C is safe)"
    )


def _spine_ref(source: ItemSource) -> SpineRef:
    """The item's provenance as the engine's ref: mirrors ``source_record``."""
    position = None if source.cut == "file" else source.index + 1
    return SpineRef(
        path=source.path or source.name,
        cut=source.cut,
        position=position,
        label=source.label,
    )


async def _fold_vectors(
    context: GraphContext, names: Sequence[str]
) -> dict[str, tuple[float, ...]]:
    """Embed the distinct surface names for the canonicalization fold — local,
    free. An unavailable embedder degrades to no folding, disclosed."""
    if len(names) < 2:
        return {}
    embedder = context.fold_embedder()
    fold_bar = _stage("fold")
    fold_bar.start(len(names))
    vectors: dict[str, tuple[float, ...]] = {}
    try:
        for batch in batched(list(names), EMBED_BATCH_SIZE):
            for name, vector in zip(batch, await embedder.embed(list(batch)), strict=True):
                vectors[name] = vector
                fold_bar.advance()
    except ItemError as exc:
        diagnostics.warn(f"entity folding skipped ({exc}) — every surface form keeps its node")
        vectors = {}
    fold_bar.finish()
    return vectors


def _save(
    raw: str, nodes: Sequence[GraphNode], edges: Sequence[GraphEdge], *, top: int | None
) -> None:
    """``--save`` dispatch by extension; ``--top`` caps the display formats
    (dot/mermaid/html) to the biggest hubs — the data formats stay complete."""
    from pathlib import Path

    fmt = save_format(raw)
    shown_nodes, shown_edges = _display_slice(nodes, edges, top)
    match fmt:
        case SaveFormat.VAULT:
            directory = Path(raw)
            directory.mkdir(parents=True, exist_ok=True)
            vault = to_obsidian(nodes, edges)
            for name, content in vault.items():
                (directory / name).write_text(content, encoding="utf-8")
            diagnostics.note(f"graph saved: {raw} ({len(vault)} notes)")
        case SaveFormat.CSV:
            path = Path(raw)
            nodes_path = path.with_suffix(".nodes.csv")
            edges_path = path.with_suffix(".edges.csv")
            nodes_path.write_text(to_nodes_csv(nodes), encoding="utf-8")
            edges_path.write_text(to_edges_csv(edges), encoding="utf-8")
            diagnostics.note(f"graph saved: {nodes_path} · {edges_path}")
        case SaveFormat.MERMAID:
            result = to_mermaid(nodes, edges, cap=top if top is not None else MERMAID_DEFAULT_CAP)
            _write(Path(raw), result.text)
            if result.shown < result.total:
                diagnostics.note(
                    f"mermaid capped to the {result.shown} biggest hubs of "
                    f"{result.total} nodes — --top adjusts it"
                )
        case SaveFormat.GRAPHML:
            _write(Path(raw), to_graphml(nodes, edges))
        case SaveFormat.DOT:
            _write(Path(raw), to_dot(shown_nodes, shown_edges))
        case SaveFormat.HTML:
            _write(Path(raw), to_html(shown_nodes, shown_edges))
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def _write(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")
    diagnostics.note(f"graph saved: {path}")


def _display_slice(
    nodes: Sequence[GraphNode], edges: Sequence[GraphEdge], top: int | None
) -> tuple[Sequence[GraphNode], Sequence[GraphEdge]]:
    if top is None or len(nodes) <= top:
        return nodes, edges
    hubs = sorted(nodes, key=lambda node: (-node.count, node.name))[:top]
    kept = {node.name for node in hubs}
    return hubs, [edge for edge in edges if edge.source in kept and edge.target in kept]
