"""The ``graph`` verb: entities and relationships as a weighted, cited graph.

Four ways in, one fold + serialize spine (waves G1/G2):

- ``--fast`` (G1): local NER + co-occurrence — zero model calls, on-device,
  free by construction (the tests pin it). This module owns that half and the
  shared machinery every mode reuses: the corpus scan, the canonicalization
  fold, the JSONL edge writer, ``--save``, and the receipt tail.
- A positional focus prompt (G2, FULL), ``--name-top`` (G2, HYBRID), and
  edge-shaped stdin records (G2, ADOPT) live in ``verbs/graphfull`` and are
  dispatched to from here — imported lazily so ``--fast`` never pays for them.

stdout is JSONL edges; ``--save`` writes graphml/dot/mermaid/csv/html or an
Obsidian vault.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from enum import Enum
from typing import TYPE_CHECKING, Protocol, assert_never

from smartpipe.core.errors import (
    ExitCode,
    ItemError,
    SempipeError,
    SetupFault,
    UnsentError,
    UsageFault,
)
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
from smartpipe.io import diagnostics, manifest, readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import describe_source, project_content
from smartpipe.io.progress import Spinner, make_stderr_spinner
from smartpipe.io.writers import RenderMode, WriterConfig, make_writer
from smartpipe.verbs.common import (
    EMBED_BATCH_SIZE,
    ExecutionPolicySource,
    batched,
    ensure_text,
    interrupted_exit_code,
    outcome_exit_code,
    spin_pending,
)
from smartpipe.verbs.common import transcribe as whisper_transcribe

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping, Sequence
    from pathlib import Path
    from typing import TextIO

    from smartpipe.engine.graphkg import EntityFinder, GraphEdge, GraphNode, SurfaceCount
    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item, ItemSource
    from smartpipe.models.base import AudioData, EmbeddingModel
    from smartpipe.models.budget import CallBudget
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.resilience import WiredChat

__all__ = [
    "DEFAULT_ENTITIES",
    "FastScan",
    "FoldCut",
    "FoldOutcome",
    "GraphContext",
    "GraphModelContext",
    "GraphRequest",
    "fold_cut_flips_partial",
    "fold_vectors",
    "note_folds",
    "parse_entities",
    "parse_relations",
    "receipt_tail",
    "run_graph",
    "save_graph",
    "scan_corpus",
    "spine_ref",
    "stage",
    "write_edges",
]

DEFAULT_ENTITIES = ("person", "organization", "location")

_PACE_SAMPLE = 20  # files before this machine's pace is worth projecting
_PACE_NOTE_S = 120.0  # projected grinds past two minutes get one honest note
_FOLD_NOTE_WINDOWS = 5_000  # co-occurrence windows past which the fold is worth naming


@dataclass(frozen=True, slots=True)
class GraphRequest:
    fast: bool = False
    focus: str | None = None  # the positional focus prompt — FULL mode (G2)
    entities: str | None = None  # comma-separated user-named types; None = the default set
    relations: str | None = None  # --relations "pays, owns": the typed-ontology enum (G2)
    name_top: int | None = None  # --name-top N: HYBRID mode (G2)
    window: str = "chunk"
    min_weight: int = 1
    save: str | None = None
    top: int | None = None  # display-format hub cap
    model_flag: str | None = None  # --model: the extraction/naming chat wire (G2)
    fallback_flag: str | None = None  # --fallback-model: chat failover when the breaker trips
    concurrency_flag: int | None = None  # --concurrency (G2)
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion (G2)
    # --embed-model: the canonicalization fold embedder (specified wins, local fallback)
    embed_model_flag: str | None = None
    input: InputSpec = STDIN


class GraphContext(Protocol):
    """What ``--fast`` needs — deliberately NO chat accessor: the free half
    cannot ask for a paid model even by accident."""

    def entity_finder(self, labels: Sequence[str]) -> EntityFinder: ...
    async def fold_embedder(self, flag: str | None = None) -> EmbeddingModel: ...


class GraphModelContext(GraphContext, ExecutionPolicySource, Protocol):
    """The paid half's seam (G2): everything ``--fast`` has, plus the composed
    resilient chat wire and its dials — the same accessors ``map`` composes with.
    The paid modes run on the returned ``WiredChat`` (breaker + concurrency gate
    with the configured fallback armed underneath), never on a plain chat model."""

    async def resilient_chat_model(
        self, flag: str | None = None, fallback_flag: str | None = None
    ) -> WiredChat: ...
    def document_parser(self, flag: str | None = None) -> DocumentParser | None: ...


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


def parse_relations(raw: str | None) -> tuple[str, ...] | None:
    """The ``--relations "pays, owns"`` dial: the closed relation vocabulary
    the model must pick from (G2's precision mode); None = free strings."""
    if raw is None:
        return None
    names = tuple(dict.fromkeys(part.strip() for part in raw.split(",") if part.strip()))
    if not names:
        raise UsageFault(
            '--relations needs at least one name\n  Example: --relations "pays, owns, employs"'
        )
    return names


async def run_graph(
    request: GraphRequest,
    context: GraphModelContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
    should_stop: Callable[[], bool] | None = None,
    transcriber: Callable[[AudioData], str] = whisper_transcribe,
    clock: Callable[[], float] = time.monotonic,
    ask: Callable[[str], bool] | None = None,
    budget: CallBudget | None = None,
) -> ExitCode:
    """Dispatch on the mode grammar: ``--name-top`` → hybrid, a focus prompt →
    full, ``--fast`` → the free half, else stdin must carry edge records.

    ``should_stop`` (B1) is the synchronous stop predicate the CPU-bound fold
    phases poll from their worker thread — a starved loop can't deliver the async
    ``stop`` in time, so the folds read this ``threading.Event``-backed callable."""
    if request.min_weight < 1:
        raise UsageFault("--min-weight needs a positive co-occurrence count")
    if request.top is not None and request.top < 1:
        raise UsageFault("--top needs a positive node count")
    if request.name_top is not None and request.name_top < 1:
        raise UsageFault("--name-top needs a positive edge count")
    if request.save is not None:
        fmt = save_format(request.save)  # a typo'd extension must refuse BEFORE the work
        _guard_save_outputs(request.save, fmt)
    if request.relations is not None and request.focus is None and request.name_top is None:
        raise UsageFault(
            "--relations shapes the model-read modes — pair it with a focus prompt "
            "or --name-top\n"
            '  Example: smartpipe graph "who pays whom" --relations "pays, owns" notes/*.md'
        )
    concurrency = context.concurrency(request.concurrency_flag)
    adopt_dispatch = not request.fast and request.focus is None and request.name_top is None
    if (
        adopt_dispatch
        and not request.input.patterns
        and not request.input.from_files
        and stdin.isatty()
    ):
        # Hoisted from run_adopt (#27): a bare terminal has no records to adopt,
        # and that usage refusal must outrank a broken embed config below.
        from smartpipe.verbs.graphfull import three_forms_fault

        raise three_forms_fault()
    # #27 preflight: build the fold embedder NOW, in every mode, so a broken embed
    # config (missing key, chat-model-as-embedder) faults at exit 2 BEFORE any
    # read, NER grind, or paid extraction. The instance is discarded — the fold
    # rebuilds it (manifest.record_model is idempotent, one fold_embed entry).
    await context.fold_embedder(request.embed_model_flag)
    if request.name_top is not None:
        from smartpipe.verbs.graphfull import run_hybrid

        return await run_hybrid(
            request,
            context,
            stdin=stdin,
            stdout=stdout,
            stop=stop,
            should_stop=should_stop,
            transcriber=transcriber,
            clock=clock,
            budget=budget,
            concurrency=concurrency,
        )
    if request.focus is not None:
        from smartpipe.verbs.graphfull import run_full

        return await run_full(
            request,
            context,
            stdin=stdin,
            stdout=stdout,
            stop=stop,
            should_stop=should_stop,
            ask=ask,
            budget=budget,
            concurrency=concurrency,
        )
    if request.fast:
        return await _run_fast(
            request,
            context,
            stdin=stdin,
            stdout=stdout,
            stop=stop,
            should_stop=should_stop,
            transcriber=transcriber,
            clock=clock,
        )
    from smartpipe.verbs.graphfull import run_adopt

    return await run_adopt(
        request, context, stdin=stdin, stdout=stdout, stop=stop, should_stop=should_stop
    )


def _guard_save_outputs(raw: str, fmt: SaveFormat) -> None:
    """Reserve the manifest boundary against every path ``--save`` writes."""
    from pathlib import Path

    path = Path(raw)
    match fmt:
        case SaveFormat.VAULT:
            manifest.guard_manifest_tree(path, role="--save vault")
        case SaveFormat.CSV:
            manifest.guard_manifest_alias(path.with_suffix(".nodes.csv"), role="--save output")
            manifest.guard_manifest_alias(path.with_suffix(".edges.csv"), role="--save output")
        case SaveFormat.GRAPHML | SaveFormat.DOT | SaveFormat.MERMAID | SaveFormat.HTML:
            manifest.guard_manifest_alias(path, role="--save output")
        case _ as unreachable:  # pragma: no cover - pyright proves exhaustiveness
            assert_never(unreachable)


class FoldCut(Enum):
    """How the canonicalization fold ended (#30) — the exit wiring keys on this."""

    NONE = "none"  # ran to completion (or fewer than two names — nothing to fold)
    INTERRUPT = "interrupt"  # a drained Ctrl-C cut it — the drain summary already tells
    FAULT = "fault"  # a mid-fold wire/content fault — degrades; the run exits by its counts
    BELT = "belt"  # --max-calls cut a PAID fold — the run flips to PARTIAL, never 0


@dataclass(frozen=True, slots=True)
class FoldOutcome:
    """What the fold salvaged: every vector embedded before any cut, plus the cut."""

    vectors: Mapping[str, tuple[float, ...]]
    cut: FoldCut


def fold_cut_flips_partial(cut: FoldCut) -> bool:
    """#29 ruling: only a BELT cut flips the run to PARTIAL — an interrupt
    already exits by the drain rules, and a mid-fold fault exits by the run's
    normal counts (ruling 5). A belt stop must also never wear the Ctrl-C
    drain summary; the interrupted branches gate on this."""
    match cut:
        case FoldCut.BELT:
            return True
        case FoldCut.NONE | FoldCut.INTERRUPT | FoldCut.FAULT:
            return False
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


@dataclass(frozen=True, slots=True)
class FastScan:
    """Everything the free pass produces — the fast mode writes it out as-is;
    the hybrid mode names the strongest edges first (wave G2)."""

    gathered: tuple[ItemEntities, ...]
    counts: tuple[SurfaceCount, ...]
    canonical: Mapping[str, str]
    folded_names: int
    nodes: tuple[GraphNode, ...]
    edges: tuple[GraphEdge, ...]
    skipped: int  # items the free ladder couldn't read (censused already)
    failed: int  # skipped items whose attempted local NER call failed
    fold_cut: FoldCut  # how the name fold ended (#30) — BELT flips the exit to PARTIAL


async def scan_corpus(
    request: GraphRequest,
    context: GraphContext,
    *,
    stdin: TextIO,
    stop: asyncio.Event | None,
    should_stop: Callable[[], bool] | None = None,
    transcriber: Callable[[AudioData], str],
    clock: Callable[[], float],
) -> FastScan | None:
    """The zero-call pass shared by ``--fast`` and hybrid: read, local NER,
    fold, co-occurrence edges. ``None`` means empty input (exit 0, silent)."""
    labels = parse_entities(request.entities)
    mode = parse_window(request.window)

    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    read_bar = stage("read")
    read_bar.start(total)
    items: list[Item] = []
    async for item in items_iter:
        items.append(item)
        read_bar.advance()
    read_bar.finish()
    if not items:
        return None

    finder = context.entity_finder(labels)
    if labels:  # empty labels never load the model (find short-circuits) — skip the ~190 MB pull
        warm_bar = make_stderr_spinner()
        await spin_pending(
            warm_bar,
            "preparing local NER model",
            asyncio.to_thread(finder.load, quiet=warm_bar.enabled),
        )
    log = diagnostics.DegradationLog()
    gathered: list[ItemEntities] = []
    no_free_text = 0
    failed = 0
    entity_bar = stage("entities")
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
                ref=spine_ref(item.source),
                doc=item.source.path or item.source.name,
                text=text,
                spans=spans,
            )
        )
        entity_bar.advance()
        if position == _PACE_SAMPLE and len(items) > _PACE_SAMPLE:
            # The bar the note points at IS ``entity_bar``; key the promise on its
            # own ``enabled`` flag so a suppressed bar never advertises "progress
            # below" (B3 — stderr-only gate; a piped stderr turns the bar off).
            _note_projected_grind(
                clock() - stage_start, len(items), progress_visible=entity_bar.enabled
            )
    entity_bar.finish()
    log.finish()
    if no_free_text:
        plural = "s" if no_free_text != 1 else ""
        diagnostics.note(
            f"{no_free_text:,} file{plural} skipped — no free text (images/scans); "
            "the full mode or ocr-model reads them"
        )

    # B1: the fold trio is CPU-bound and can dominate a large corpus (the
    # co-occurrence fold is quadratic in the entities per window). Run it OFF the
    # event loop so a single Ctrl-C is delivered and the fold-stage bar redraws;
    # the pure fold polls ``should_stop`` per window and salvages a clean partial.
    counts = await asyncio.to_thread(surface_counts, gathered)
    fold = await fold_vectors(
        context,
        [surface.name for surface in counts],
        request.embed_model_flag,
        should_stop=should_stop,
    )
    canonical = await asyncio.to_thread(
        fold_surfaces, counts, fold.vectors, should_stop=should_stop
    )
    folded_names, folded_nodes = fold_stats(canonical)
    note_folds(folded_names, folded_nodes)
    nodes = await asyncio.to_thread(build_nodes, counts, canonical)
    entity_windows = windows(gathered, mode)
    _note_fold_phase(len(entity_windows))
    fold_bar = stage("fold")
    fold_bar.start(len(entity_windows))
    edges = await asyncio.to_thread(
        fold_edges, entity_windows, canonical, should_stop=should_stop, progress=fold_bar.advance
    )
    fold_bar.finish()

    return FastScan(
        gathered=tuple(gathered),
        counts=counts,
        canonical=canonical,
        folded_names=folded_names,
        nodes=nodes,
        edges=edges,
        skipped=len(items) - len(gathered),
        failed=failed,
        fold_cut=fold.cut,
    )


async def _run_fast(
    request: GraphRequest,
    context: GraphContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
    should_stop: Callable[[], bool] | None,
    transcriber: Callable[[AudioData], str],
    clock: Callable[[], float],
) -> ExitCode:
    scan = await scan_corpus(
        request,
        context,
        stdin=stdin,
        stop=stop,
        should_stop=should_stop,
        transcriber=transcriber,
        clock=clock,
    )
    if scan is None:
        return outcome_exit_code(done=0, skipped=0, failed=0)
    kept, pruned = prune_edges(scan.edges, request.min_weight)
    write_edges(kept, stdout)
    if request.save is not None:
        save_graph(request.save, scan.nodes, kept, top=request.top)
    diagnostics.note(
        f"graph: {len(scan.counts):,} entities ({scan.folded_names:,} folded) · "
        f"{len(kept):,} edges ({pruned:,} pruned) · 0 tok"
    )
    fold_partial = fold_cut_flips_partial(scan.fold_cut)
    if stop is not None and stop.is_set() and not fold_partial:
        # A drained Ctrl-C (during entity extraction or the fold, B1): the graph
        # written above is salvaged partial — say so and exit accordingly. A
        # BELT cut set the same stop event without any Ctrl-C (#29), so it must
        # never wear the drain summary — it exits PARTIAL below instead.
        diagnostics.interrupted_summary(processed=len(scan.gathered), skipped=scan.skipped)
        return interrupted_exit_code(
            done=len(scan.gathered),
            skipped=scan.skipped,
            failed=scan.failed,
            partial=True,
        )
    return outcome_exit_code(
        done=len(scan.gathered),
        skipped=scan.skipped,
        failed=scan.failed,
        partial=fold_partial,
    )


def stage(name: str) -> Spinner:
    """The G0 bar wearing this stage's name (a ``run`` pipeline's label wins)."""
    spinner = make_stderr_spinner()
    if spinner.label is None:
        spinner.label = name
    return spinner


def note_folds(folded_names: int, folded_nodes: int) -> None:
    """The canonicalization disclosure every mode shares (silent when nothing folded)."""
    if folded_names:
        plural = "s" if folded_nodes != 1 else ""
        diagnostics.note(f"{folded_names} entity names folded into {folded_nodes} node{plural}")


def receipt_tail() -> str:
    """The receipt's cost segment: the run's observed tokens, or an honest zero."""
    from smartpipe.io import metering

    return metering.receipt() or "0 tok"


def write_edges(edges: Sequence[GraphEdge], stdout: TextIO) -> None:
    """The JSONL result stream every mode shares: one edge per line, weighted,
    with spine-ref provenance — behind the ``write`` stage bar."""
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=None), stdout
    )
    write_bar = stage("write")
    write_bar.start(len(edges))
    for edge in edges:
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


def _note_projected_grind(elapsed: float, total_files: int, *, progress_visible: bool) -> None:
    """Projected-time honesty (owner ruling): after the sample, this machine's
    measured pace projects the whole entity pass — past ~2 minutes, say so once.
    The unit is FILES (the pass iterates items), not windows — the fold's own
    windows are named separately by ``_note_fold_phase`` (B1). The "(progress
    below …)" clause only rides along when the status bar is actually animating
    (``progress_visible``); with a suppressed bar the projection stays truthful
    but promises nothing it won't deliver (B3)."""
    projected = elapsed / _PACE_SAMPLE * total_files
    if projected <= _PACE_NOTE_S:
        return
    minutes = max(1, round(projected / 60))
    line = f"~{total_files:,} files at this machine's pace — roughly {minutes} min"
    if progress_visible:
        line += " (progress below; Ctrl-C is safe)"
    diagnostics.note(line)


def _note_fold_phase(total_windows: int) -> None:
    """B1: once the entities are gathered, the co-occurrence fold is a distinct
    quadratic phase that can dominate on large corpora. Name it (past a threshold,
    so small graphs stay quiet) so a user watching the entity pass finish isn't
    left wondering why the run keeps going."""
    if total_windows < _FOLD_NOTE_WINDOWS:
        return
    diagnostics.note(
        f"entities done — folding {total_windows:,} windows; "
        "this can dominate on large corpora (progress below; Ctrl-C is safe)"
    )


def spine_ref(source: ItemSource) -> SpineRef:
    """The item's provenance as the engine's ref: mirrors ``source_record``."""
    position = None if source.cut == "file" else source.index + 1
    return SpineRef(
        path=source.path or source.name,
        cut=source.cut,
        position=position,
        label=source.label,
    )


async def fold_vectors(
    context: GraphContext,
    names: Sequence[str],
    embed_flag: str | None = None,
    *,
    should_stop: Callable[[], bool] | None = None,
) -> FoldOutcome:
    """Embed the distinct surface names for the canonicalization fold. The
    embedder honors the configured ``embed-model``/``--embed-model`` (specified
    wins) and falls back to the on-device local model when nothing is set. A
    non-local (paid) fold is disclosed once, since it spends even on ``--fast``.

    Paid work is never lost (#30): every vector embedded before a cut is kept.
    ``should_stop`` — the SYNCHRONOUS Ctrl-C predicate, polled once per batch;
    never the async drain event, which the belt shares and must not cut a free
    fold — ends it at INTERRUPT. The belt's ``UnsentError`` ends it at BELT; any
    other mid-fold fault (a wire dying mid-run included, ruling 5) degrades to
    FAULT. Unembedded names simply keep their spelling downstream. Build faults
    are NOT caught here: the ``run_graph`` preflight (#27) owns those, fatally."""
    if len(names) < 2:
        return FoldOutcome(vectors={}, cut=FoldCut.NONE)
    embedder = await context.fold_embedder(embed_flag)  # build faults stay fatal (#27)
    # local is on-device and ollama is free loopback/self-hosted (as convert.py and
    # fence.py already treat it) — neither spends, so only a paid cloud wire discloses.
    if embedder.ref.provider not in ("local", "ollama"):
        diagnostics.note(
            f"folding {len(names):,} entity names via {embedder.ref} (paid embeddings)"
        )
    fold_bar = stage("fold")
    fold_bar.start(len(names))
    vectors: dict[str, tuple[float, ...]] = {}
    cut = FoldCut.NONE
    try:
        for batch in batched(list(names), EMBED_BATCH_SIZE):
            if should_stop is not None and should_stop():
                cut = FoldCut.INTERRUPT
                diagnostics.note(
                    f"entity folding interrupted — {len(vectors):,} of {len(names):,} "
                    "names embedded; the rest keep their spelling"
                )
                break
            for name, vector in zip(batch, await embedder.embed(list(batch)), strict=True):
                vectors[name] = vector
                fold_bar.advance()
    except UnsentError as exc:  # an ItemError subclass — the belt arm must come FIRST
        cut = FoldCut.BELT
        _warn_fold_cut(exc, embedded=len(vectors), total=len(names))
    except (ItemError, SetupFault) as exc:
        cut = FoldCut.FAULT
        _warn_fold_cut(exc, embedded=len(vectors), total=len(names))
    fold_bar.finish()
    return FoldOutcome(vectors=vectors, cut=cut)


def _warn_fold_cut(exc: SempipeError, *, embedded: int, total: int) -> None:
    """The mid-fold degradation disclosure: what was kept, what keeps its spelling.
    The nothing-embedded wording stays byte-identical to the pre-#30 skip line."""
    if embedded == 0:
        diagnostics.warn(f"entity folding skipped ({exc}) — every surface form keeps its node")
        return
    diagnostics.warn(
        f"entity folding stopped early ({exc}) — {embedded:,} of {total:,} "
        "names embedded; the rest keep their spelling"
    )


def save_graph(
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
