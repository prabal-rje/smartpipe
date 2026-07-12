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
from typing import TYPE_CHECKING

from smartpipe.core.errors import (
    ExitCode,
    ItemError,
    SetupFault,
    SourceCounts,
    TooManyFailures,
    UsageFault,
    is_recoverable_item_error,
)
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
from smartpipe.io.progress import make_stderr_spinner
from smartpipe.io.tty import tty_asker
from smartpipe.models.base import AudioData, ImageData, VideoData
from smartpipe.verbs.common import interrupted_exit_code, outcome_exit_code, spin_pending
from smartpipe.verbs.graph import (
    FastScan,
    FoldCut,
    GraphModelContext,
    GraphRequest,
    fold_cut_flips_partial,
    fold_vectors,
    note_dense_graph,
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
    from smartpipe.models.base import ChatModel
    from smartpipe.models.budget import CallBudget
    from smartpipe.models.stt import Transcriber

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
_CANARY_SNIPPET = "Alice pays Bob for the shipment."  # A2: the pre-spend schema probe
# The hybrid probe must exercise the SAME payload the naming loop drives — a
# pair plus a co-occurrence window (see _naming_item) — not a bare sentence, so
# it proves the model can name from a pair window, not merely emit relation JSON
# for free text (GLM review NIT 5). It still embeds _CANARY_SNIPPET verbatim so
# the receipt/test filters that key on that marker recognise it.
_CANARY_NAMING_SNIPPET = (
    f"source: Alice\ntarget: Bob\n\nthey appear together in:\n[1] {_CANARY_SNIPPET}"
)

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


def _canary_affordable(budget: CallBudget | None) -> bool:
    """The canary is a real belt call, so skip it when the belt can't also
    afford at least one unit of real work: a probe that drains the whole belt
    leaves nothing to protect, and a belt of one has at most one call to guard
    (GLM review — this is what keeps `--max-calls 1` doing its one real call
    instead of burning the unit on the probe, and empty stdin at a belt of one
    from spending anything at all)."""
    return budget is None or budget.limit - budget.calls >= 2


async def _schema_canary(
    model: ChatModel,
    plan: MapPlan,
    instruction: str,
    log: diagnostics.DegradationLog,
    *,
    what: str,
    snippet: str = _CANARY_SNIPPET,
) -> None:
    """Fire ONE synthetic extraction through the compiled schema before any
    ingestion or paid OCR (A2). A model that cannot hold ``what`` would otherwise
    burn the whole paid run on unparseable replies — pilot run B spent 943 OCR
    pages and 7 extractions before a wholesale schema halt. ``map_one`` already
    grants the one shape-repair rung, so a canary that still comes back wrong is
    total incapacity, not a fluke; a rerun's identical probe is a cache hit, so
    the check costs nothing the second time. An availability fault at canary time
    (belt exhausted, 429 ladder spent, breaker open) is NOT a capability verdict
    — it propagates as itself, never relabeled."""
    canary = Item(
        raw=snippet,
        text=snippet,
        data=None,
        source=ItemSource(kind="stdin", name="schema canary", index=0),
    )
    try:
        await map_one(model, plan, instruction, canary, log)
    except ItemError as failed:
        if not is_recoverable_item_error(failed):
            raise  # belt/429/breaker — terminal, and no statement about the schema
        raise SetupFault(
            f"error: {model.ref} cannot hold {what}\n"
            "  A canary extraction on a fixed snippet came back the wrong shape,\n"
            "  so this model would burn the whole run on unparseable replies.\n"
            "  Try --model openai/gpt-5.4-nano (or your configured fallback), or\n"
            "  run --fast for the free co-occurrence graph."
        ) from failed


async def run_full(
    request: GraphRequest,
    context: GraphModelContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
    should_stop: Callable[[], bool] | None = None,
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
    # The resilient stack: primary wire + breaker + concurrency gate, the configured
    # fallback armed lazily underneath (item 11). `model` IS the resilient callable —
    # a provider-down trip swaps to the backup inside it and the worker never branches
    # on the wire's health; the canary below runs on the primary, and a swap during it
    # is fine. An embed-ref fallback is refused at this line, pre-spend.
    wired = await context.resilient_chat_model(request.model_flag, request.fallback_flag)
    model = wired.model  # may have emitted a note / SetupFault during resolution
    ocr = readers.OcrIngest.lazy(lambda: context.document_parser(request.ocr_model_flag), log)
    # A8: the OCR read phase wears the belt too — a scan corpus larger than the
    # remaining --max-calls asks before spending (a decline reads nothing, exit 0).
    items_iter, total = readers.resolve_items(
        request.input, stdin, stop=stop, ocr=ocr, budget=budget, ask=ask
    )
    if total != 0 and _canary_affordable(budget):
        # A2: prove the model holds the schema BEFORE iterating spends paid OCR.
        # A known-empty file list (total == 0) has nothing to protect; a stdin
        # stream (total is None) always probes, since its size is unknowable
        # here — unless the belt can't afford the probe plus real work. The
        # probe can sit on a cold wire for the whole retry ladder, so it wears
        # the pinned pending caption while it runs (D4 #37).
        await spin_pending(
            make_stderr_spinner(),
            "checking the model holds the schema",
            _schema_canary(model, plan, instruction, log, what="the extraction schema"),
        )
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
            log.skip(describe_source(item.source), str(exc))  # B4: bucketed, not one line/chunk
            failed_sources.add(owner)
            continue
        if not made:
            empty_sources.add(owner)
            continue
        expected_chunks[owner] = len(made)
        chunks.extend(made)
        chunk_owners.extend([owner] * len(made))
    if not chunks:
        log.finish()  # flush any chunk-skip rollup (B4) — no extraction phase to do it
        source_counts = _source_counts(items, succeeded=empty_sources, failed=failed_sources)
        return outcome_exit_code(
            done=len(empty_sources),
            skipped=len(items) - len(empty_sources),
            failed=len(failed_sources),
            source_counts=source_counts,
        )

    # the pre-flight cost plan (ledger 66f): the note prints BEFORE any spend.
    # A2: the canary (and any read-phase OCR) has already charged the belt, so
    # the partial-run test must read what REMAINS, not the raw limit — else a
    # belt sized exactly to the chunk count silently yields a partial after
    # promising a full graph (GLM review SHOULD-FIX 1). The note reports that
    # REMAINING, not the raw limit, so the shortfall the user must close is
    # honest: a belt of 3 on 3 chunks shows "2 left" (the probe took one), not a
    # "belt is 3" that reads as exactly-enough and understates by one probe unit.
    belt = budget.limit if budget is not None else None
    # budget is not None is redundant given belt (belt None ⟺ budget None) but
    # pyright cannot span the two statements — it narrows budget.calls here.
    remaining = belt - budget.calls if belt is not None and budget is not None else None
    plural = "s" if len(items) != 1 else ""
    plan_note = f"~{len(chunks):,} extraction calls across {len(items):,} file{plural}"
    belt_short = remaining is not None and remaining < len(chunks)
    if belt_short:
        assert remaining is not None  # belt_short ⟹ remaining is not None
        plan_note += f"; {remaining:,} left in the belt — the graph will be partial"
    elif belt is None and len(chunks) > _NUDGE_CALLS:
        plan_note += " — no belt set"
    diagnostics.note(plan_note)
    if belt_short:
        asker = ask if ask is not None else tty_asker(stdin)
        if asker is not None and not asker(CONFIRM_PARTIAL):
            from smartpipe.io import manifest

            manifest.abandon()
            log.finish()  # flush any chunk-skip rollup (B4) before the declined-plan exit
            return ExitCode.OK  # declined at the plan: nothing spent

    async def worker(chunk: Item) -> tuple[Item, str | Mapping[str, object]]:
        # Capture the answering ref at entry (mirrors map's `slot.current`): after a
        # swap the receipt must count under the wire that answered, not the dead
        # primary. Graph's chunks are pre-cut, so `answering`'s only use is the tally.
        answering = wired.answering_ref()
        result = await map_one(model, plan, instruction, chunk, log)
        wired.tally(answering)  # count one answered chunk under the wire captured at entry
        return chunk, result

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
        fallback_armed=wired.armed,
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
                log.skip(describe_source(outcome.source), outcome.reason)  # B4: bucketed
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
    # halt re-raise. The fold itself salvages too (#30): fold_vectors keeps every
    # vector embedded before a Ctrl-C, a mid-fold wire fault, or the belt
    # (FoldOutcome), so a cut fold means fewer folded names — never a lost run.
    # B1: the fold trio runs OFF the event loop so a Ctrl-C during it is
    # delivered and the bar redraws; fold_surfaces polls ``should_stop`` and
    # degrades to a clean partial canonical map on a stop. fold_assertions runs
    # UNGATED (the deliberate B1-review rollback): a latched SIGINT would cut it
    # at assertion #1 and the salvage would write an EMPTY graph — the write
    # below is cheap and must carry everything already paid for.
    counts = await asyncio.to_thread(assertion_surface_counts, assertions)
    fold = await fold_vectors(
        context,
        [surface.name for surface in counts],
        request.embed_model_flag,
        should_stop=should_stop,
    )
    surface_bar = stage("fold")  # D4 (#37): the label-cluster fold stays visible
    surface_bar.start(None)
    try:  # C2 review: a cancel/fault mid-fold must still clear the row + deregister
        canonical = await asyncio.to_thread(
            fold_surfaces,
            counts,
            fold.vectors,
            should_stop=should_stop,
            progress=surface_bar.advance,
        )
    finally:
        surface_bar.finish()
    folded_names, folded_nodes = fold_stats(canonical)
    note_folds(folded_names, folded_nodes)
    nodes = await asyncio.to_thread(build_nodes, counts, canonical)
    folded_edges = await asyncio.to_thread(fold_assertions, assertions, canonical)
    kept, _ = prune_edges(folded_edges, request.min_weight)
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
    if wired.switched:
        diagnostics.note(wired.receipt())  # the failover seam stays visible (item 11)
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
    # The stop event conflates a Ctrl-C with the belt's own drain, and the belt
    # latch is HISTORICAL (review blocker): the last extraction can exactly
    # exhaust the belt (run complete, stop set, no shortfall) BEFORE the user
    # Ctrl-Cs the free fold. The fold's own INTERRUPT report is the truth about
    # a real Ctrl-C there, so it overrides the latch — never silently swallowed.
    if (
        stop is not None
        and stop.is_set()
        and (fold.cut is FoldCut.INTERRUPT or not (budget is not None and budget.exhausted))
    ):
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
        # a belt-cut FOLD also flips the exit (#29) — the extraction may be
        # complete, but the graph's canonicalization is not.
        partial=belt_partial or fold_cut_flips_partial(fold.cut),
        source_counts=source_counts,
    )


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
    should_stop: Callable[[], bool] | None = None,
    transcriber: Callable[[AudioData], str],
    clock: Callable[[], float],
    budget: CallBudget | None,
    concurrency: int,
    stt: Transcriber | None = None,
) -> ExitCode:
    assert request.name_top is not None  # dispatched here on exactly that
    relations = parse_relations(request.relations)
    scan = await scan_corpus(
        request,
        context,
        stdin=stdin,
        stop=stop,
        should_stop=should_stop,
        transcriber=transcriber,
        clock=clock,
        stt=stt,
    )
    if scan is None:
        return outcome_exit_code(done=0, skipped=0, failed=0)
    kept, _ = prune_edges(scan.edges, request.min_weight)
    note_dense_graph(len(scan.nodes), len(kept))  # #34: BEFORE the paid naming spend
    want = min(request.name_top, len(kept))
    candidates = kept[:want]  # already sorted heaviest first — the N strongest

    named: dict[tuple[str, str], str] = {}
    naming_skips = 0
    naming_halted = False
    if candidates:
        # The resilient stack (item 11): primary naming wire + breaker + gate, the
        # configured fallback armed lazily underneath. `model` IS the resilient
        # callable — a provider-down trip swaps to the backup and the worker never
        # branches on health; the canary below runs on the primary.
        wired = await context.resilient_chat_model(request.model_flag, request.fallback_flag)
        model = wired.model
        plan = MapPlan("structured", _naming_schema(relations), MAP_JSON_SYSTEM)
        instruction = _naming_instruction(request.focus)
        log = diagnostics.DegradationLog()
        # A2: hybrid's only paid phase is naming (the scan is free), so the canary
        # sits here — fired only when there are edges to name (never on empty
        # input) and only when the belt can afford the probe plus real naming.
        # The probe drives the naming-shaped payload, not a bare sentence (NIT 5).
        if _canary_affordable(budget):
            await spin_pending(  # the pinned pending caption rides here too (D4 #37)
                make_stderr_spinner(),
                "checking the model holds the schema",
                _schema_canary(
                    model,
                    plan,
                    instruction,
                    log,
                    what="the relation-naming schema",
                    snippet=_CANARY_NAMING_SNIPPET,
                ),
            )

        async def worker(item: Item) -> tuple[int, str]:
            answering = wired.answering_ref()  # captured at entry, like map's slot.current
            result = await map_one(model, plan, instruction, item, log)
            from collections.abc import Mapping as MappingABC

            assert isinstance(result, MappingABC)  # the plan is structured
            relation = result.get("relation")
            if not isinstance(relation, str) or not relation.strip():
                raise ItemError("the model named no relation")
            wired.tally(answering)  # count one named edge under the wire captured at entry
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
            fallback_armed=wired.armed,
        )
        try:
            async for outcome in outcomes:
                if isinstance(outcome, Done):
                    position, relation = outcome.value
                    edge = candidates[position]
                    named[edge.source, edge.target] = relation
                else:  # Skipped — that edge keeps co-occurs, nothing lost
                    naming_skips += 1
                    log.skip(describe_source(outcome.source), outcome.reason)  # B4: bucketed
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
        if wired.switched:
            diagnostics.note(wired.receipt())  # the failover seam stays visible (item 11)

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
    # The same historical-latch trap as run_full (review blocker), carried out
    # through FastScan.fold_cut: a fold-reported INTERRUPT is a real Ctrl-C and
    # overrides an exhausted belt; a cut fold also makes the salvage partial.
    if (
        stop is not None
        and stop.is_set()
        and (scan.fold_cut is FoldCut.INTERRUPT or not (budget is not None and budget.exhausted))
    ):
        diagnostics.interrupted_summary(processed=len(scan.gathered), skipped=scan.skipped)
        return interrupted_exit_code(
            done=len(scan.gathered),
            skipped=scan.skipped,
            failed=scan.failed,
            partial=bool(naming_skips) or scan.fold_cut is FoldCut.INTERRUPT,
        )
    return outcome_exit_code(
        done=len(scan.gathered),
        skipped=scan.skipped,
        failed=scan.failed,
        # a belt-cut FOLD in the scan also flips the exit (#29), carried out
        # through FastScan.fold_cut — even when there was nothing to name.
        partial=belt_short or bool(naming_skips) or fold_cut_flips_partial(scan.fold_cut),
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
    should_stop: Callable[[], bool] | None = None,
) -> ExitCode:
    """Edge records in, graph out — zero extraction calls: adopt each row's
    endpoints/relation/weight/provenance, canonicalize, fold, serialize.

    The bare-terminal three-forms refusal lives in ``run_graph`` (#27): it must
    outrank the fold-embedder preflight that fires there before dispatch."""
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

    # B1: the fold trio runs off the event loop (see run_full) so a Ctrl-C is
    # delivered even on a large adopted corpus. fold_vectors salvages on any cut
    # (#30) and fold_assertions runs UNGATED (the B1-review rollback, see
    # run_full): a latched SIGINT must not zero the write below.
    counts = await asyncio.to_thread(assertion_surface_counts, assertions)
    fold = await fold_vectors(
        context,
        [surface.name for surface in counts],
        request.embed_model_flag,
        should_stop=should_stop,
    )
    surface_bar = stage("fold")  # D4 (#37): the label-cluster fold stays visible
    surface_bar.start(None)
    try:  # C2 review: a cancel/fault mid-fold must still clear the row + deregister
        canonical = await asyncio.to_thread(
            fold_surfaces,
            counts,
            fold.vectors,
            should_stop=should_stop,
            progress=surface_bar.advance,
        )
    finally:
        surface_bar.finish()
    folded_names, folded_nodes = fold_stats(canonical)
    note_folds(folded_names, folded_nodes)
    nodes = await asyncio.to_thread(build_nodes, counts, canonical)
    folded_edges = await asyncio.to_thread(fold_assertions, assertions, canonical)
    kept, pruned = prune_edges(folded_edges, request.min_weight)
    write_edges(kept, stdout)
    if request.save is not None:
        save_graph(request.save, nodes, kept, top=request.top)
    diagnostics.note(
        f"graph: {len(counts):,} entities ({folded_names:,} folded) · "
        f"{len(kept):,} edges ({pruned:,} pruned) · 0 tok"
    )
    fold_partial = fold_cut_flips_partial(fold.cut)
    if stop is not None and stop.is_set() and not fold_partial:
        # B1 review: a drained Ctrl-C during the fold salvaged a partial graph on
        # stdout — say so and exit INTERRUPTED/PARTIAL, never OK (like run_full/
        # hybrid). A BELT cut set the same stop event with no Ctrl-C (#29): it
        # must never wear the drain summary — it exits PARTIAL below instead.
        diagnostics.interrupted_summary(processed=len(assertions), skipped=0)
        return interrupted_exit_code(done=len(assertions), skipped=0, failed=0, partial=True)
    return outcome_exit_code(done=len(assertions), skipped=0, failed=0, partial=fold_partial)


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
