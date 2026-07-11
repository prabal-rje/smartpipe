"""The ``graph`` verb's paid half (wave G2): full extraction, hybrid naming,
adopted pipe-in edges. Three modes, one fold + serialize spine shared with G1.

- FULL — a positional focus prompt: chunk with split's units (tokens for
  text/docs, minutes for a/v), extract ``{triples {subject, relation,
  object}[]}`` per chunk through map's media ladders, then fold and serialize.
  The cost plan prints BEFORE any spend (ledger 66f); the ``--max-calls`` belt
  drains at the cap into a disclosed partial graph that never exits 0.
- HYBRID — ``--name-top N``: the free co-occurrence pass builds candidates,
  then one call per edge names the relation; a belt shortfall keeps
  ``co-occurs`` on the remainder — nothing lost, disclosed.
- ADOPT — edge-shaped records on stdin skip extraction entirely: adopt,
  canonicalize, fold, serialize — the power path after a custom ``extend``.
"""

from __future__ import annotations

import asyncio
import sys
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode, ItemError, SourceCounts, TooManyFailures, UsageFault
from smartpipe.engine.graphkg import (
    EdgeAssertion,
    assertion_surface_counts,
    build_nodes,
    fold_assertions,
    fold_stats,
    fold_surfaces,
    name_edges,
    prune_edges,
    spine_from_record,
)
from smartpipe.engine.prompts import (
    MAP_JSON_SYSTEM,
    MapPlan,
    parse_prompt,
    plan_map,
    to_instruction,
)
from smartpipe.engine.runner import Done, run_ordered
from smartpipe.io import diagnostics, readers, source_accounting
from smartpipe.io.items import Item, ItemSource, describe_source, project_content
from smartpipe.models.base import AudioData, ImageData, VideoData
from smartpipe.verbs.common import interrupted_exit_code, outcome_exit_code
from smartpipe.verbs.graph import (
    FastScan,
    GraphModelContext,
    GraphRequest,
    fold_vectors,
    note_folds,
    parse_entities,
    parse_relations,
    receipt_tail,
    save_graph,
    scan_corpus,
    spine_ref,
    stage,
    write_edges,
)
from smartpipe.verbs.map import map_one

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Callable, Mapping, Sequence
    from typing import TextIO

    from smartpipe.engine.graphkg import GraphEdge, SpineRef
    from smartpipe.models.budget import CallBudget

__all__ = [
    "CONFIRM_PARTIAL",
    "chunk_assertions",
    "extraction_prompt",
    "run_adopt",
    "run_full",
    "run_hybrid",
    "three_forms_fault",
    "tty_asker",
]

_CHUNK_TOKENS = 2_000  # split's default token budget — the shared chunk size
_AV_SLICE_SECONDS = 600  # split's default duration unit: --by minutes → 10-minute slices
_NUDGE_CALLS = 100  # beltless plans past this many calls earn the nudge
_SNIPPET_CAP = 3  # co-occurrence context windows per naming call
_SNIPPET_CHARS = 400  # each window arrives trimmed — context, not the corpus

CONFIRM_PARTIAL = "proceed with a partial graph? [y/N]"


def three_forms_fault(where: str | None = None) -> UsageFault:
    """The refusal matrix: no ``--fast``, no focus prompt, no edge records."""
    head = (
        f"{where} isn't an edge record — graph needs one of its three forms"
        if where is not None
        else "graph needs one of its three forms"
    )
    return UsageFault(
        f"{head}\n"
        "  --fast                   the free co-occurrence mode — local NER, zero model calls\n"
        '  a focus prompt           graph "who pays whom" notes/*.md — model extraction\n'
        '  edge records on stdin    {"source", "target"} or {"subject", "relation", "object"} '
        "rows"
    )


# --- FULL: focus-prompt extraction ------------------------------------------------


def extraction_prompt(
    focus: str, labels: tuple[str, ...] | None, relations: tuple[str, ...] | None
) -> str:
    """The object-list braces prompt (one grammar, one path): the focus prompt
    is the instruction preamble; ``--entities``/``--relations`` compile to enum
    constraints on the endpoint types and the relation (precision mode)."""
    fields = ["subject string"]
    if labels is not None:
        fields.append(f"subject_type enum({', '.join(labels)})")
    fields.append(f"relation enum({', '.join(relations)})" if relations else "relation string")
    fields.append("object string")
    if labels is not None:
        fields.append(f"object_type enum({', '.join(labels)})")
    group = "{triples {" + ", ".join(fields) + "}[]}"
    return (
        f"{focus}\n\nExtract {group}: every relationship this item asserts, "
        "with short canonical entity names."
    )


async def run_full(
    request: GraphRequest,
    context: GraphModelContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
    ask: Callable[[str], bool] | None,
    budget: CallBudget | None,
    concurrency: int,
) -> ExitCode:
    assert request.focus is not None  # dispatched here on exactly that
    labels = parse_entities(request.entities) if request.entities is not None else None
    relations = parse_relations(request.relations)
    tokens = parse_prompt(
        extraction_prompt(request.focus, labels, relations), allow_descriptions=True
    )
    plan = plan_map(tokens, schema=None)
    instruction = to_instruction(tokens)

    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop, ocr=ocr)
    read_bar = stage("read")
    read_bar.start(total)
    items: list[Item] = []
    async for item in items_iter:
        items.append(item)
        read_bar.advance()
    read_bar.finish()
    if not items:
        return outcome_exit_code(done=0, skipped=0, failed=0)

    chunks: list[Item] = []
    chunk_owners: list[int] = []
    expected_chunks: dict[int, int] = {}
    empty_sources: set[int] = set()
    failed_sources: set[int] = set()
    for owner, item in enumerate(items):
        try:
            made = await _chunk_item(item)
        except ItemError as exc:
            diagnostics.warn(f"skipped: {describe_source(item.source)} ({exc})")
            failed_sources.add(owner)
            continue
        if not made:
            empty_sources.add(owner)
            continue
        expected_chunks[owner] = len(made)
        chunks.extend(made)
        chunk_owners.extend([owner] * len(made))
    if not chunks:
        source_counts = _source_counts(items, succeeded=empty_sources, failed=failed_sources)
        return outcome_exit_code(
            done=len(empty_sources),
            skipped=len(items) - len(empty_sources),
            failed=len(failed_sources),
            source_counts=source_counts,
        )

    # the pre-flight cost plan (ledger 66f): the note prints BEFORE any spend
    belt = budget.limit if budget is not None else None
    plural = "s" if len(items) != 1 else ""
    plan_note = f"~{len(chunks):,} extraction calls across {len(items):,} file{plural}"
    belt_short = belt is not None and belt < len(chunks)
    if belt_short:
        plan_note += f"; belt is {belt:,} — the graph will be partial"
    elif belt is None and len(chunks) > _NUDGE_CALLS:
        plan_note += " — no belt set"
    diagnostics.note(plan_note)
    if belt_short:
        asker = ask if ask is not None else tty_asker(stdin)
        if asker is not None and not asker(CONFIRM_PARTIAL):
            from smartpipe.io import manifest

            manifest.abandon()
            return ExitCode.OK  # declined at the plan: nothing spent

    model = await context.chat_model(request.model_flag)  # may emit a note / SetupFault

    async def worker(chunk: Item) -> tuple[Item, str | Mapping[str, object]]:
        return chunk, await map_one(model, plan, instruction, chunk, log)

    assertions: list[EdgeAssertion] = []
    done = 0
    chunk_skipped = 0
    completed_chunks: dict[int, int] = {}
    extract_bar = stage("extract")
    extract_bar.start(len(chunks))
    outcomes = run_ordered(
        _iter_items(chunks),
        worker,
        concurrency=concurrency,
        failure_policy=context.failure_policy(model.ref.provider),
        stop=stop,
    )
    halted: TooManyFailures | None = None
    try:
        outcome_position = 0
        async for outcome in outcomes:
            owner = chunk_owners[outcome_position]
            outcome_position += 1
            if isinstance(outcome, Done):
                chunk, result = outcome.value
                assertions.extend(chunk_assertions(result, spine_ref(chunk.source)))
                done += 1
                completed_chunks[owner] = completed_chunks.get(owner, 0) + 1
            else:  # Skipped — the union has no third case
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                chunk_skipped += 1
                if outcome.failed:
                    failed_sources.add(owner)
            extract_bar.advance()
    except TooManyFailures as exc:
        halted = exc  # the failure policy tripped mid-extraction — salvage, then re-raise
    finally:
        extract_bar.finish()
        log.finish()

    # Salvage runs through the SAME fold/write path a clean exit takes: the
    # already-extracted assertions are folded, pruned, and written below BEFORE any
    # halt re-raise. The fold is a prerequisite (edges need canonical names), so if
    # the fold wire itself fails here the resulting SetupFault is fatal for a clean
    # run too — it is orthogonal to A1's extraction-halt salvage, not a regression
    # of it (fold-path resilience is tracked separately as B1).
    counts = assertion_surface_counts(assertions)
    vectors = await fold_vectors(context, [surface.name for surface in counts])
    canonical = fold_surfaces(counts, vectors)
    folded_names, folded_nodes = fold_stats(canonical)
    note_folds(folded_names, folded_nodes)
    nodes = build_nodes(counts, canonical)
    kept, _ = prune_edges(fold_assertions(assertions, canonical), request.min_weight)
    write_edges(kept, stdout)
    if request.save is not None:
        save_graph(request.save, nodes, kept, top=request.top)

    ok_sources = empty_sources | {
        owner
        for owner, expected in expected_chunks.items()
        if completed_chunks.get(owner, 0) == expected and owner not in failed_sources
    }
    skipped_sources = len(items) - len(ok_sources)
    source_counts = _source_counts(items, succeeded=ok_sources, failed=failed_sources)

    belt_partial = budget is not None and budget.exhausted and done < len(chunks)
    if belt_partial:
        diagnostics.note(
            f"belt hit — {done:,} of {len(chunks):,} chunks extracted; the graph is partial "
            "(rerun raises the belt; cache makes it cheap)"
        )
    diagnostics.note(
        f"graph: {len(counts):,} entities ({folded_names:,} folded) · "
        f"{len(kept):,} edges · {receipt_tail()}"
    )
    if halted is not None:
        # the failure policy tripped mid-extraction: the fold + write above
        # SALVAGED every chunk that landed before it (run B lost 7 extractions and
        # 943 paid OCR pages to this exact gap). Re-raise carrying the verb's
        # FILE-unit counts — the runner halts on chunks, but the manifest accounts
        # sources — so settled() finalizes ALL_FAILED on the same books a clean
        # exit shows, with the salvaged edges already on stdout (ux.md exit-3 screen).
        raise TooManyFailures(
            halted.failed,
            halted.total,
            halted.last_reason,
            source_counts=source_counts,
        ) from halted
    if stop is not None and stop.is_set() and not (budget is not None and budget.exhausted):
        diagnostics.interrupted_summary(processed=done, skipped=chunk_skipped)
        return interrupted_exit_code(
            done=len(ok_sources),
            skipped=skipped_sources,
            failed=len(failed_sources),
            partial=True,
            source_counts=source_counts,
        )
    return outcome_exit_code(
        done=len(ok_sources),
        skipped=skipped_sources,
        failed=len(failed_sources),
        partial=belt_partial,
        source_counts=source_counts,
    )


def tty_asker(stdin: TextIO) -> Callable[[str], bool] | None:
    """The one y/N confirm, TTY-only: piped stdin (data) or piped stderr (cron)
    can't ask — the plan note stands and the belt governs."""
    from smartpipe.io import tty

    if not (stdin.isatty() and tty.stderr_is_tty()):
        return None

    def ask(question: str) -> bool:
        sys.stderr.write(f"{question} ")
        sys.stderr.flush()
        return stdin.readline().strip().lower() in ("y", "yes")

    return ask


async def _chunk_item(item: Item) -> list[Item]:
    """One item → its extraction chunks, split's units reused (D26 layer 3):
    tokens for text, 10-minute slices for a/v, one call per embedded figure."""
    projected = project_content(item)
    origin = describe_source(projected.source)
    chunks: list[Item] = []
    if projected.text.strip():
        from smartpipe.engine.chunking import split_text

        parts = split_text(projected.text, _CHUNK_TOKENS)
        chunks.extend(
            _text_chunk(projected, part, position, len(parts), origin)
            for position, part in enumerate(parts, start=1)
        )
    lone_media = len(projected.media) == 1 and not projected.text.strip()
    figure_count = 0
    for part in projected.media:
        if isinstance(part, AudioData | VideoData):
            chunks.extend(await asyncio.to_thread(_clip_chunks, projected, part, origin))
            continue
        assert isinstance(part, ImageData)  # the media union is closed
        figure_count += 1
        if lone_media:
            source = projected.source
        else:
            source = _cut_source(
                projected.source, f"{origin} img.{figure_count}", figure_count - 1, "file"
            )
        chunks.append(Item(raw="", text="", data=None, source=source, media=(part,)))
    return chunks


def _text_chunk(item: Item, part: str, position: int, total: int, origin: str) -> Item:
    source = (
        item.source
        if total == 1
        else _cut_source(item.source, f"{origin} §{position}/{total}", position - 1, "tokens")
    )
    return Item(raw=part, text=part, data=None, source=source, media=())


def _clip_chunks(item: Item, clip: AudioData | VideoData, origin: str) -> list[Item]:
    """Duration slices (split's ``--by minutes``): each rides map's a/v ladder
    on its own — native where the wire hears/watches, converted where not."""
    from smartpipe.parsing.extract import slice_audio, slice_video

    parts: Sequence[AudioData | VideoData] = (
        slice_video(clip, seconds=_AV_SLICE_SECONDS)
        if isinstance(clip, VideoData)
        else slice_audio(clip, seconds=_AV_SLICE_SECONDS)
    )
    chunks: list[Item] = []
    for position, part in enumerate(parts):
        source = (
            item.source
            if len(parts) == 1
            else _cut_source(
                item.source,
                f"{origin} §{_clock(position * _AV_SLICE_SECONDS)}-"
                f"{_clock((position + 1) * _AV_SLICE_SECONDS)}",
                position,
                "minutes",
            )
        )
        chunks.append(Item(raw="", text="", data=None, source=source, media=(part,)))
    return chunks


def _cut_source(source: ItemSource, label: str, index: int, cut: str) -> ItemSource:
    return ItemSource(
        kind=source.kind,
        name=label,
        index=index,
        cut=cut,
        path=source.path or source.name,
        label=label,
    )


def _clock(seconds: int) -> str:
    return f"{seconds // 60:02d}:{seconds % 60:02d}"


def chunk_assertions(result: str | Mapping[str, object], ref: SpineRef) -> list[EdgeAssertion]:
    """One chunk's validated reply → its assertions, deduped within the chunk
    so an edge's weight counts chunks asserting it, not repetitions."""
    from collections.abc import Mapping as MappingABC

    from smartpipe.core.jsontools import as_items, as_record

    assert isinstance(result, MappingABC)  # the plan is structured by construction
    seen: set[tuple[str, str, str]] = set()
    assertions: list[EdgeAssertion] = []
    for entry in as_items(result.get("triples")) or ():
        record = as_record(entry)
        if record is None:
            continue
        subject = _clean(record.get("subject"))
        relation = _clean(record.get("relation"))
        target = _clean(record.get("object"))
        if subject is None or relation is None or target is None:
            continue
        key = (subject, relation, target)
        if key in seen:
            continue
        seen.add(key)
        assertions.append(
            EdgeAssertion(
                refs=(ref,),
                source=subject,
                relation=relation,
                target=target,
                source_label=_clean(record.get("subject_type")) or "entity",
                target_label=_clean(record.get("object_type")) or "entity",
            )
        )
    return assertions


def _clean(value: object) -> str | None:
    if isinstance(value, str) and value.strip():
        return value.strip()
    return None


async def _iter_items(items: Sequence[Item]) -> AsyncIterator[Item]:
    for item in items:
        yield item


def _source_counts(
    items: Sequence[Item],
    *,
    succeeded: set[int],
    failed: set[int],
) -> SourceCounts:
    """Fold paid graph chunk outcomes back onto their accepted sources."""
    sources = source_accounting.SourceCounter()
    for owner, item in enumerate(items):
        if owner in succeeded:
            sources.done(item.source)
        else:
            sources.skip(item.source, failed=owner in failed)
    return sources.counts


# --- HYBRID: --name-top N ----------------------------------------------------------


async def run_hybrid(
    request: GraphRequest,
    context: GraphModelContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
    transcriber: Callable[[AudioData], str],
    clock: Callable[[], float],
    budget: CallBudget | None,
    concurrency: int,
) -> ExitCode:
    assert request.name_top is not None  # dispatched here on exactly that
    relations = parse_relations(request.relations)
    scan = await scan_corpus(
        request, context, stdin=stdin, stop=stop, transcriber=transcriber, clock=clock
    )
    if scan is None:
        return outcome_exit_code(done=0, skipped=0, failed=0)
    kept, _ = prune_edges(scan.edges, request.min_weight)
    want = min(request.name_top, len(kept))
    candidates = kept[:want]  # already sorted heaviest first — the N strongest

    named: dict[tuple[str, str], str] = {}
    naming_skips = 0
    naming_halted = False
    if candidates:
        model = await context.chat_model(request.model_flag)
        plan = MapPlan("structured", _naming_schema(relations), MAP_JSON_SYSTEM)
        instruction = _naming_instruction(request.focus)
        log = diagnostics.DegradationLog()

        async def worker(item: Item) -> tuple[int, str]:
            result = await map_one(model, plan, instruction, item, log)
            from collections.abc import Mapping as MappingABC

            assert isinstance(result, MappingABC)  # the plan is structured
            relation = result.get("relation")
            if not isinstance(relation, str) or not relation.strip():
                raise ItemError("the model named no relation")
            return item.source.index, relation.strip()

        asks = [_naming_item(position, edge, scan) for position, edge in enumerate(candidates)]
        name_bar = stage("name")
        name_bar.start(len(asks))
        outcomes = run_ordered(
            _iter_items(asks),
            worker,
            concurrency=concurrency,
            failure_policy=context.failure_policy(model.ref.provider),
            stop=stop,
        )
        try:
            async for outcome in outcomes:
                if isinstance(outcome, Done):
                    position, relation = outcome.value
                    edge = candidates[position]
                    named[edge.source, edge.target] = relation
                else:  # Skipped — that edge keeps co-occurs, nothing lost
                    naming_skips += 1
                    diagnostics.warn(
                        f"skipped: {describe_source(outcome.source)} ({outcome.reason})"
                    )
                name_bar.advance()
        except TooManyFailures:
            # the naming model failed the schema on too many edges. Unlike full
            # mode, the FREE co-occurrence graph is already whole — only the
            # enhancement stopped. Salvage it below (unnamed edges keep co-occurs)
            # and exit PARTIAL: the sources all succeeded, so ALL_FAILED would lie.
            naming_halted = True
            diagnostics.note(
                "naming stopped early — too many edges failed the schema; "
                "the strongest remain co-occurs"
            )
        finally:
            name_bar.finish()
            log.finish()

    belt_short = budget is not None and budget.exhausted and len(named) < want
    if belt_short:
        diagnostics.note(
            f"named {len(named):,} of {want:,} (belt); "
            f"{want - len(named):,} strongest remain co-occurs"
        )
    renamed = name_edges(kept, named)
    write_edges(renamed, stdout)
    if request.save is not None:
        save_graph(request.save, scan.nodes, renamed, top=request.top)
    diagnostics.note(
        f"graph: {len(scan.counts):,} entities ({scan.folded_names:,} folded) · "
        f"{len(renamed):,} edges · {len(named):,} named · {receipt_tail()}"
    )
    if naming_halted:  # the co-occurrence graph was salvaged above — partial, not fatal
        return outcome_exit_code(
            done=len(scan.gathered),
            skipped=scan.skipped,
            failed=scan.failed,
            partial=True,
        )
    if stop is not None and stop.is_set() and not (budget is not None and budget.exhausted):
        diagnostics.interrupted_summary(processed=len(scan.gathered), skipped=scan.skipped)
        return interrupted_exit_code(
            done=len(scan.gathered),
            skipped=scan.skipped,
            failed=scan.failed,
            partial=bool(naming_skips),
        )
    return outcome_exit_code(
        done=len(scan.gathered),
        skipped=scan.skipped,
        failed=scan.failed,
        partial=belt_short or bool(naming_skips),
    )


def _naming_schema(relations: tuple[str, ...] | None) -> dict[str, object]:
    from smartpipe.engine.schema import shorthand_to_schema

    prop: dict[str, object] = (
        {"enum": list(relations)} if relations is not None else {"type": "string"}
    )
    return shorthand_to_schema(["relation"], types={"relation": prop})


def _naming_instruction(focus: str | None) -> str:
    base = (
        "Name the relationship from source to target as a short verb phrase "
        '(like "pays" or "reports to"), reading the passages where they appear together.'
    )
    return f"{base}\nFocus: {focus}" if focus is not None else base


def _naming_item(position: int, edge: GraphEdge, scan: FastScan) -> Item:
    """One naming call's payload: the pair plus up to three co-occurrence
    context windows — enough evidence to name, never the whole corpus."""
    lines = [f"source: {edge.source}", f"target: {edge.target}"]
    snippets = _pair_snippets(scan, edge.source, edge.target)
    if snippets:
        lines.extend(["", "they appear together in:"])
        lines.extend(f"[{n}] {snippet}" for n, snippet in enumerate(snippets, start=1))
    text = "\n".join(lines)
    source = ItemSource(
        kind="stdin", name=f"edge {edge.source} — {edge.target}", index=position, cut="lines"
    )
    return Item(raw=text, text=text, data=None, source=source)


def _pair_snippets(scan: FastScan, source: str, target: str) -> list[str]:
    snippets: list[str] = []
    for entry in scan.gathered:
        present = {scan.canonical.get(span.name, span.name) for span in entry.spans}
        if source in present and target in present:
            snippets.append(entry.text[:_SNIPPET_CHARS])
            if len(snippets) == _SNIPPET_CAP:
                break
    return snippets


# --- ADOPT: edge-shaped records on stdin --------------------------------------------


async def run_adopt(
    request: GraphRequest,
    context: GraphModelContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
) -> ExitCode:
    """Edge records in, graph out — zero extraction calls: adopt each row's
    endpoints/relation/weight/provenance, canonicalize, fold, serialize."""
    if not request.input.patterns and not request.input.from_files and stdin.isatty():
        raise three_forms_fault()  # a bare terminal has no records to adopt
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    read_bar = stage("read")
    read_bar.start(total)
    assertions: list[EdgeAssertion] = []
    async for item in items_iter:
        assertions.append(_adopt_assertion(item))  # non-edge rows refuse loudly
        read_bar.advance()
    read_bar.finish()
    if not assertions:
        raise three_forms_fault()

    counts = assertion_surface_counts(assertions)
    vectors = await fold_vectors(context, [surface.name for surface in counts])
    canonical = fold_surfaces(counts, vectors)
    folded_names, folded_nodes = fold_stats(canonical)
    note_folds(folded_names, folded_nodes)
    nodes = build_nodes(counts, canonical)
    kept, pruned = prune_edges(fold_assertions(assertions, canonical), request.min_weight)
    write_edges(kept, stdout)
    if request.save is not None:
        save_graph(request.save, nodes, kept, top=request.top)
    diagnostics.note(
        f"graph: {len(counts):,} entities ({folded_names:,} folded) · "
        f"{len(kept):,} edges ({pruned:,} pruned) · 0 tok"
    )
    return outcome_exit_code(done=len(assertions), skipped=0, failed=0)


def _adopt_assertion(item: Item) -> EdgeAssertion:
    """One stdin record as an assertion: ``{subject, relation, object}`` or
    ``{source, target}`` (graph's own output shape) — anything else refuses
    with the three-forms screen. Weight and ``sources`` provenance are adopted."""
    data = item.data
    if data is None:
        raise three_forms_fault(where=describe_source(item.source))
    subject = _clean(data.get("subject"))
    relation = _clean(data.get("relation"))
    target = _clean(data.get("object"))
    if subject is None or target is None:
        subject = _clean(data.get("source"))
        target = _clean(data.get("target"))
        relation = relation or "co-occurs"
        if subject is None or target is None:
            raise three_forms_fault(where=describe_source(item.source))
    elif relation is None:
        raise three_forms_fault(where=describe_source(item.source))
    return EdgeAssertion(
        refs=_adopted_refs(item),
        source=subject,
        relation=relation,
        target=target,
        weight=_adopted_weight(data),
    )


def _adopted_weight(data: Mapping[str, object]) -> int:
    weight = data.get("weight")
    if isinstance(weight, int) and not isinstance(weight, bool) and weight >= 1:
        return weight
    return 1


def _adopted_refs(item: Item) -> tuple[SpineRef, ...]:
    from smartpipe.core.jsontools import as_items, as_record

    assert item.data is not None  # narrowed by the caller
    carried = tuple(
        ref
        for entry in as_items(item.data.get("sources")) or ()
        if (record := as_record(entry)) is not None
        and (ref := spine_from_record(record)) is not None
    )
    return carried if carried else (spine_ref(item.source),)
