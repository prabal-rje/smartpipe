"""The ``join`` verb (D21): match stdin against a second input, semantically.

Embed → block → judge: the right side (``--right``) is finite — read whole,
embedded once (chunked), held as an in-memory index; each left item embeds,
blocks to its top-K nearest candidates, and only those pairs reach the chat
model with the filter-style verdict schema. N·K calls, never N·M.

Fail-before-spend order (D18): flags → grammar → right file exists/parses/
non-empty → right side fully embedded → cost preview → the first judge call.
A bad right side costs zero chat calls.
"""

from __future__ import annotations

import asyncio
import json
from dataclasses import dataclass, replace
from functools import partial
from typing import TYPE_CHECKING, Protocol

from smartpipe.core.errors import ExitCode, ItemError, TooManyFailures, UsageFault
from smartpipe.engine.blocking import RightIndex, build_index, candidates
from smartpipe.engine.chunking import estimate_tokens, mean_pool, split_text
from smartpipe.engine.prompts import (
    JUDGE_SCHEMA,
    build_judge_request,
    build_repair_request,
    parse_join_predicate,
)
from smartpipe.engine.ranking import rank, select
from smartpipe.engine.runner import (
    Done,
    FailurePolicy,
    run_ordered,
    should_halt,
    should_halt_consecutive,
)
from smartpipe.engine.schema import validate_and_coerce
from smartpipe.io import diagnostics, readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.items import ItemSource, describe_source, item_from_line, source_record
from smartpipe.io.progress import make_stderr_spinner
from smartpipe.verbs.common import (
    ModelSlot,
    breaker_policy,
    embed_budget,
    embed_in_batches,
    ensure_text,
    interrupted_exit_code,
    make_failover,
    outcome_exit_code,
)
from smartpipe.verbs.convert import Converter, make_converter
from smartpipe.verbs.oversize import RowNote, judge_bisected, matched_note, refusal

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from smartpipe.engine.prompts import Token
    from smartpipe.io.inputs import InputSpec
    from smartpipe.io.items import Item
    from smartpipe.io.readers import OcrIngest
    from smartpipe.io.writers import OutputFormat, ResultWriter, TextSink
    from smartpipe.models.base import ChatModel, EmbeddingModel, ModelRef
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.stt import RemoteTranscriber

__all__ = ["JoinContext", "JoinRequest", "PairBook", "run_join"]

_PREVIEW_THRESHOLD = 200  # estimated judge calls before the cost line appears (D21)


@dataclass(frozen=True, slots=True)
class JoinRequest:
    predicate: str | None
    right: Path
    k: int
    threshold: float | None
    model_flag: str | None
    embed_model_flag: str | None
    concurrency_flag: int | None
    output: OutputFormat
    input: InputSpec = STDIN
    fields: tuple[str, ...] | None = None
    unmatched: Path | None = None  # write zero-match left items here, verbatim
    allow_captions: bool = False  # cloud conversions opt-in (D33)
    kind: str = "inner"  # inner | leftouter | anti (D38/11)
    fallback_flag: str | None = None  # --fallback-model: chat failover when the breaker trips
    bare: bool = False  # --bare: strip __ metadata from record output (item 18)
    on: tuple[str, ...] = ()  # --on 'left.F == right.F' (repeatable, AND-ed) — item 21
    full: bool = False  # --full: disable the TTY preview's truncation (item 19)
    whole: bool = False  # --whole: refuse oversized sides instead of chunk-judging (D26 v2)
    ocr_model_flag: str | None = None  # --ocr-model: document parsing at ingestion (item 48)


class JoinContext(Protocol):
    def remote_transcriber(self, chat_ref: ModelRef | None = None) -> RemoteTranscriber | None: ...

    """The first verb that needs BOTH models — the container already has both."""

    def document_parser(self, flag: str | None = None) -> DocumentParser | None: ...
    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    def fallback_ref(self, flag: str | None = None) -> ModelRef | None: ...
    async def fallback_chat_model(self, ref: ModelRef) -> ChatModel: ...
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...
    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextSink,
        fields: tuple[str, ...] | None = None,
        bare: bool = False,
        full: bool = False,
    ) -> ResultWriter: ...


@dataclass(slots=True)
class PairBook:
    """Judge-call accounting (D21: the halt policies count judge calls)."""

    policy: FailurePolicy
    right_name: str
    judged: int = 0
    skipped: int = 0
    consecutive: int = 0
    succeeded: bool = False

    def ok(self) -> None:
        self.judged += 1
        self.consecutive = 0
        self.succeeded = True

    def skip(self, left: Item, right_position: int, reason: str) -> None:
        self.judged += 1
        self.skipped += 1
        self.consecutive += 1
        diagnostics.warn(
            f"skipped: {describe_source(left.source)} × "  # noqa: RUF001 — pinned pair mark
            f"{self.right_name} line {right_position + 1} ({reason})"
        )
        if should_halt(self.policy, total=self.judged, skipped=self.skipped):
            raise TooManyFailures(self.skipped, self.judged, reason)
        if should_halt_consecutive(
            self.policy, succeeded=self.succeeded, consecutive=self.consecutive
        ):
            raise TooManyFailures(self.skipped, self.judged, reason)


async def run_join(
    request: JoinRequest,
    context: JoinContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
) -> ExitCode:
    on_pairs = _parse_on(request.on)
    if request.predicate is None and on_pairs is None:
        raise UsageFault(
            "join needs a predicate or --on\n"
            '  Semantic: smartpipe join "ticket {left.text} concerns {right.name}" --right …\n'
            "  Deterministic: smartpipe join --on 'left.sku == right.sku' --right …"
        )
    if request.k < 1:
        raise UsageFault(f"--k must be >= 1, got {request.k}")
    log = diagnostics.DegradationLog()  # per-row conversion disclosure (D27)
    parser = context.document_parser(request.ocr_model_flag)  # the ocr-model role (item 48)
    ocr = readers.OcrIngest(parser, log) if parser is not None else None
    if request.predicate is None:
        assert on_pairs is not None
        return await _run_key_join(
            request, on_pairs, context, stdin=stdin, stdout=stdout, stop=stop, ocr=ocr
        )
    tokens = parse_join_predicate(request.predicate)  # UsageFault on bad grammar
    right_items = await _load_right_items(request.right, ocr)
    embed_model = await context.embedding_model(request.embed_model_flag)
    kept_right, index, right_chunks = await _index_right(
        embed_model, right_items, request.right.name, log, whole=request.whole
    )
    chat = await context.chat_model(request.model_flag)
    slot = ModelSlot(chat)
    fallback = context.fallback_ref(request.fallback_flag)  # embed refs refused here (free)
    concurrency = context.concurrency(request.concurrency_flag)
    if request.kind not in ("inner", "leftouter", "anti"):
        raise UsageFault("--kind takes inner, leftouter, or anti")
    if request.kind == "anti" and request.unmatched is not None:
        raise UsageFault(
            "--unmatched with --kind anti is redundant — anti already puts unmatched rows on stdout"
        )
    spinner = make_stderr_spinner()
    # the arbiter: result writes pause the status line, so they never interleave
    writer = context.writer(
        request.output,
        structured=request.kind != "anti",  # anti emits left rows verbatim
        stdout=spinner.guard(stdout),
        fields=request.fields,
        bare=request.bare,
        full=request.full,
    )
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop, ocr=ocr)
    preview_cost(total, request.k, len(index))

    converter = make_converter(
        chat,
        allow_paid=request.allow_captions,
        log=log,
        stt=context.remote_transcriber(chat.ref),
        ocr=parser,
    )
    book = PairBook(policy=FailurePolicy(), right_name=request.right.name)
    right_blocks = _right_blocks(kept_right, on_pairs)
    avoided = [0]  # pairs never considered thanks to equality blocking (item 21)
    spinner.start(total=total)

    async def worker(item: Item) -> tuple[Item, tuple[tuple[int, float], ...]]:
        current = slot.current  # captured per item: the failover swaps wholesale
        block: list[int] | None = None
        if on_pairs is not None and right_blocks is not None:
            key = _key_of(item, tuple(left for left, _right in on_pairs))
            block = right_blocks.get(key, []) if key is not None else []
            avoided[0] += len(kept_right) - len(block)
        matches = await _join_one(
            item,
            log=log,
            converter=converter,
            embed_model=embed_model,
            chat=current,
            tokens=tokens,
            index=index,
            kept_right=kept_right,
            right_chunks=right_chunks,
            request=request,
            book=book,
            stop=stop,
            block=block,
        )
        slot.tally(str(current.ref))
        return item, matches

    policy = breaker_policy(chat.ref.provider)
    failover = (
        make_failover(
            slot, partial(context.fallback_chat_model, fallback), limit=policy.transport_limit
        )
        if fallback is not None
        else None
    )
    done = 0
    skipped = 0
    outcomes = run_ordered(
        items_iter,
        worker,
        concurrency=concurrency,
        failure_policy=policy,
        stop=stop,
        failover=failover,
    )
    matched_pairs = 0
    unmatched_count = 0
    unmatched_sink = (
        request.unmatched.open("w", encoding="utf-8") if request.unmatched is not None else None
    )
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                left, matches = outcome.value
                if request.kind != "anti":
                    for position, score in matches:
                        writer.write_record(
                            {
                                "left": _payload(left),
                                "right": _payload(kept_right[position]),
                                "__score": round(score, 4),
                                "__sources": _pair_sources(left, kept_right[position]),
                            }
                        )
                matched_pairs += len(matches)
                if not matches:
                    unmatched_count += 1
                    match request.kind:
                        case "anti":  # the unmatched row IS the finding — verbatim
                            writer.write_text(left.raw)
                        case "leftouter":  # every left row, match or not
                            writer.write_record({"left": _payload(left), "right": None})
                        case _:
                            if unmatched_sink is not None:
                                unmatched_sink.write(left.raw + "\n")
                done += 1
            else:  # Skipped — the left item itself failed (image, embed error, …)
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
        log.finish()
        if unmatched_sink is not None:
            unmatched_sink.close()
    if request.unmatched is not None:
        diagnostics.note(
            f"join: {matched_pairs} matched · {unmatched_count} unmatched → "
            f"{request.unmatched.name}"
        )
    elif request.kind != "inner":
        diagnostics.note(f"join: {matched_pairs} matched · {unmatched_count} unmatched")
    if slot.switched:
        diagnostics.note(slot.receipt())  # the seam stays visible (item 11)
    if avoided[0]:
        diagnostics.note(f"join --on: {avoided[0]:,} pairs never considered (equality blocking)")
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=done, skipped=skipped + book.skipped)
        return interrupted_exit_code(done=done, skipped=skipped + book.skipped)
    return outcome_exit_code(done=done, skipped=skipped + book.skipped)


async def _join_one(
    item: Item,
    *,
    log: diagnostics.DegradationLog,
    converter: Converter,
    embed_model: EmbeddingModel,
    chat: ChatModel,
    tokens: tuple[Token, ...],
    index: RightIndex,
    kept_right: list[Item],
    right_chunks: dict[int, ChunkedSide],
    request: JoinRequest,
    book: PairBook,
    stop: asyncio.Event | None,
    block: list[int] | None = None,
) -> tuple[tuple[int, float], ...]:
    item = await ensure_text(item, log=log, converter=converter)  # D33 ladder
    budget = embed_budget(embed_model.ref.provider)
    left_side: ChunkedSide | None = None
    if estimate_tokens(item.text) > budget:
        if request.whole:
            # --whole: the old refusal — reproducibility beats handling (D26 v2)
            raise ItemError(refusal(estimate_tokens(item.text), embed_model.ref.name, budget))
        vector, left_side = await _chunked_side(embed_model, item.text, budget)
        log.note(
            describe_source(item.source),
            "oversized → any-chunk judge",
            f"{len(left_side.chunks)} chunks pooled for blocking",
        )
    else:
        vector = (await embed_model.embed([item.text]))[0]
    matches: list[tuple[int, float]] = []
    if block is not None:
        # --on with a prompt (item 21): embedding + judge run ONLY within the
        # equality block — the same select() semantics, restricted
        ranked = select(
            rank(vector, [index.vectors[p] for p in block]),
            k=request.k,
            threshold=request.threshold,
        )
        pairs = tuple((block[i], score) for i, score in ranked)
    else:
        pairs = candidates(vector, index, k=request.k, threshold=request.threshold)
    note = RowNote(describe_source(item.source))  # item 3: re-splits, once per row
    for position, score in pairs:
        if stop is not None and stop.is_set():
            break
        right_item = kept_right[position]
        chunked_right = right_chunks.get(position)
        # D26 v2: a chunked side judges chunk-wise, best-first, ANY-true —
        # early exit on the first matching chunk keeps the cost win
        left_texts = (
            left_side.ordered(index.vectors[position]) if left_side is not None else (item.text,)
        )
        right_texts = (
            chunked_right.ordered(vector) if chunked_right is not None else (right_item.text,)
        )
        try:
            verdict = await _judge_pair(
                chat, tokens, item, right_item, left_texts, right_texts, note=note
            )
        except ItemError as exc:
            book.skip(item, position, str(exc))
            continue
        book.ok()
        if verdict:
            matches.append((position, score))
    return tuple(matches)


async def _judge_pair(
    chat: ChatModel,
    tokens: tuple[Token, ...],
    left: Item,
    right: Item,
    left_texts: tuple[str, ...],
    right_texts: tuple[str, ...],
    *,
    note: RowNote,
) -> bool:
    """One pair's verdict; chunked sides OR their chunk verdicts (D26 v2),
    and the disclosure names the chunk that matched. Auto-chunks the wire
    still rejects with a context 400 bisect (item 3) — join's chunks are
    embed-budget-sized, which a small local chat window may not hold."""
    total = len(left_texts) * len(right_texts)
    left_chunked = len(left_texts) > 1  # ChunkedSide always cuts ≥ 2 chunks
    right_chunked = len(right_texts) > 1
    position = 0
    for left_text in left_texts:
        for right_text in right_texts:
            position += 1
            if left_chunked:
                # bisect the machine-cut LEFT chunk; the right text rides fixed

                async def judge_left(text: str, fixed_right: str = right_text) -> bool:
                    return await _judge(
                        chat, tokens, replace(left, text=text), replace(right, text=fixed_right)
                    )

                verdict = await judge_bisected(judge_left, left_text, note=note)
            elif right_chunked:
                # bisect the machine-cut RIGHT chunk; the left text rides fixed

                async def judge_right(text: str, fixed_left: str = left_text) -> bool:
                    return await _judge(
                        chat, tokens, replace(left, text=fixed_left), replace(right, text=text)
                    )

                verdict = await judge_bisected(judge_right, right_text, note=note)
            else:
                # both sides whole: user boundaries — a wire rejection stays a
                # pair error (never re-cut what the user cut)
                verdict = await _judge(
                    chat, tokens, replace(left, text=left_text), replace(right, text=right_text)
                )
            if verdict:
                if total > 1:
                    diagnostics.note(matched_note(describe_source(left.source), position, total))
                return True
    return False


def _parse_on(expressions: tuple[str, ...]) -> tuple[tuple[str, str], ...] | None:
    """--on 'left.FIELD == right.FIELD' (item 21): repeatable, AND-ed."""
    if not expressions:
        return None
    import re

    pairs: list[tuple[str, str]] = []
    pattern = re.compile(r"^left\.([A-Za-z_][A-Za-z0-9_]*)\s*==\s*right\.([A-Za-z_][A-Za-z0-9_]*)$")
    for expression in expressions:
        matched = pattern.match(expression.strip())
        if matched is None:
            raise UsageFault(
                f"--on wants left.FIELD == right.FIELD, got {expression!r}\n"
                "  Example: --on 'left.sku == right.sku'   (repeat --on to AND more keys)"
            )
        pairs.append((matched.group(1), matched.group(2)))
    return tuple(pairs)


def _key_of(item: Item, fields: tuple[str, ...]) -> tuple[str, ...] | None:
    """The item's equality key: canonicalized field values; None when any key
    field is missing — a missing key never equals anything."""
    record = item.data if item.data is not None else {"text": item.text}
    values: list[str] = []
    for name in fields:
        value = record.get(name)
        if value is None:
            return None
        values.append(
            value
            if isinstance(value, str)
            else json.dumps(value, sort_keys=True, separators=(",", ":"))
        )
    return tuple(values)


def _right_blocks(
    kept_right: list[Item], on_pairs: tuple[tuple[str, str], ...] | None
) -> dict[tuple[str, ...], list[int]] | None:
    if on_pairs is None:
        return None
    fields = tuple(right for _left, right in on_pairs)
    blocks: dict[tuple[str, ...], list[int]] = {}
    for position, right_item in enumerate(kept_right):
        key = _key_of(right_item, fields)
        if key is not None:
            blocks.setdefault(key, []).append(position)
    return blocks


async def _run_key_join(
    request: JoinRequest,
    on_pairs: tuple[tuple[str, str], ...],
    context: JoinContext,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None,
    ocr: OcrIngest | None = None,
) -> ExitCode:
    """--on alone (item 21): a deterministic key-equality join — zero model
    calls (a configured ocr-model at ingestion is the one exception, item 48),
    works with --kind inner/leftouter/anti and --unmatched."""
    right_items = await _load_right_items(request.right, ocr)
    blocks = _right_blocks(right_items, on_pairs)
    assert blocks is not None
    spinner = make_stderr_spinner()
    writer = context.writer(
        request.output,
        structured=request.kind != "anti",
        stdout=spinner.guard(stdout),
        fields=request.fields,
        bare=request.bare,
    )
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop, ocr=ocr)
    spinner.start(total=total)
    left_fields = tuple(left for left, _right in on_pairs)
    done = 0
    matched_pairs = 0
    unmatched_count = 0
    unmatched_sink = (
        request.unmatched.open("w", encoding="utf-8") if request.unmatched is not None else None
    )
    try:
        async for left in items_iter:
            if stop is not None and stop.is_set():
                break
            key = _key_of(left, left_fields)
            positions = blocks.get(key, []) if key is not None else []
            if request.kind != "anti":
                for position in positions:
                    writer.write_record(
                        {
                            "left": _payload(left),
                            "right": _payload(right_items[position]),
                            "__sources": _pair_sources(left, right_items[position]),
                        }
                    )
            matched_pairs += len(positions)
            if not positions:
                unmatched_count += 1
                match request.kind:
                    case "anti":
                        writer.write_text(left.raw)
                    case "leftouter":
                        writer.write_record({"left": _payload(left), "right": None})
                    case _:
                        if unmatched_sink is not None:
                            unmatched_sink.write(left.raw + "\n")
            done += 1
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
        if ocr is not None:
            ocr.log.finish()  # the OCR disclosures' rollup (item 48)
        if unmatched_sink is not None:
            unmatched_sink.close()
    if request.unmatched is not None:
        diagnostics.note(
            f"join: {matched_pairs} matched · {unmatched_count} unmatched → "
            f"{request.unmatched.name}"
        )
    elif request.kind != "inner":
        diagnostics.note(f"join: {matched_pairs} matched · {unmatched_count} unmatched")
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=done, skipped=0)
        return interrupted_exit_code(done=done, skipped=0)
    return outcome_exit_code(done=done, skipped=0)


def _payload(item: Item) -> dict[str, object]:
    return dict(item.data) if item.data is not None else {"text": item.text}


def _pair_sources(left: Item, right: Item) -> list[dict[str, object]]:
    """Item 64: a synthesized pair carries BOTH parents' spine refs — left's,
    then right's, in compact ``source_record`` form."""
    return [source_record(left.source), source_record(right.source)]


async def _judge(chat: ChatModel, tokens: tuple[Token, ...], left: Item, right: Item) -> bool:
    """One verdict with the standard single repair; ItemError = the pair skips."""
    request = build_judge_request(tokens, left, right)
    reply = await chat.complete(request)
    try:
        verdict = validate_and_coerce(reply, JUDGE_SCHEMA)
    except ItemError as first_error:
        repair = build_repair_request(request, bad_reply=reply, error=str(first_error))
        verdict = validate_and_coerce(await chat.complete(repair), JUDGE_SCHEMA)
    return verdict.get("match") is True


async def _load_right_items(path: Path, ocr: OcrIngest | None) -> list[Item]:
    """The right side under the ocr-model role (item 48): a parseable
    PDF/image --right parses to page items (disclosed per page, the belt
    applies); anything else — and an unset role — reads exactly as before
    (JSONL or plain lines)."""
    if ocr is not None and str(path) != "-" and path.exists():
        parsed = await readers.ocr_parse_file(path, 0, ocr)
        if parsed:
            return parsed
    return _load_right(path)


def _load_right(path: Path) -> list[Item]:
    if str(path) == "-":
        raise UsageFault(
            "--right - reads nothing — stdin is join's left side\n"
            "  The right side is a finite file smartpipe indexes up front.\n"
            '  Example: cat stream.jsonl | smartpipe join "…" --right catalog.jsonl'
        )
    if not path.exists():
        raise UsageFault(
            f"no such file: {path}\n  --right needs a JSONL or plain-lines file to index."
        )
    lines = path.read_text(encoding="utf-8").splitlines(keepends=True)
    items = [
        replace(
            item_from_line(line, index),
            source=ItemSource("file", path.name, index),
        )
        for index, line in enumerate(lines)
    ]
    if not items:
        raise UsageFault(
            f"{path} is empty — a join against nothing is a mistake\n"
            '  join needs a right side to match against. If you meant "keep nothing", '
            "that's filter."
        )
    return items


@dataclass(frozen=True, slots=True)
class ChunkedSide:
    """An oversized side's chunks + their vectors — the ANY-true judge reads
    them best-first, so the early exit is the cheap path (D26 v2)."""

    chunks: tuple[str, ...]
    vectors: tuple[tuple[float, ...], ...]

    def ordered(self, other_vector: tuple[float, ...]) -> tuple[str, ...]:
        return tuple(self.chunks[position] for position, _ in rank(other_vector, self.vectors))


async def _chunked_side(
    model: EmbeddingModel, text: str, budget: int
) -> tuple[tuple[float, ...], ChunkedSide]:
    """Chunk-embed one oversized text: pooled vector for blocking, chunks kept
    so the judge reads the most-relevant one (D26/W3 — no more skipping)."""
    chunks = split_text(text, budget)
    vectors = await model.embed(list(chunks))
    return mean_pool(vectors), ChunkedSide(tuple(chunks), tuple(vectors))


async def _index_right(
    model: EmbeddingModel,
    items: list[Item],
    right_name: str,
    log: diagnostics.DegradationLog,
    *,
    whole: bool = False,
) -> tuple[list[Item], RightIndex, dict[int, ChunkedSide]]:
    """The build side, fully embedded before any chat spend (the preflight)."""
    budget = embed_budget(model.ref.provider)
    normal = [item for item in items if estimate_tokens(item.text) <= budget]
    oversized = [item for item in items if estimate_tokens(item.text) > budget]
    kept: list[Item] = []
    vectors: list[tuple[float, ...]] = []
    chunked: dict[int, ChunkedSide] = {}
    async for outcome in embed_in_batches(model, normal, failure_policy=FailurePolicy()):
        if isinstance(outcome, Done):
            item, vector = outcome.value
            kept.append(item)
            vectors.append(vector)
        else:
            diagnostics.warn(f"skipped: {right_name} line {outcome.index + 1} ({outcome.reason})")
    for item in oversized:
        if whole:
            # --whole: the old refusal — reproducibility beats handling (D26 v2)
            why = refusal(estimate_tokens(item.text), model.ref.name, budget)
            diagnostics.warn(f"skipped: {right_name} line {item.source.index + 1} ({why})")
            continue
        try:
            pooled, side = await _chunked_side(model, item.text, budget)
        except ItemError as exc:
            diagnostics.warn(f"skipped: {right_name} line {item.source.index + 1} ({exc})")
            continue
        chunked[len(kept)] = side
        kept.append(item)
        vectors.append(pooled)
        log.note(
            f"{right_name} line {item.source.index + 1}",
            "oversized → any-chunk judge",
            f"{len(side.chunks)} chunks pooled for blocking",
        )
    return kept, build_index(vectors), chunked


def preview_cost(total: int | None, k: int, index_size: int) -> None:
    per_item = min(k, index_size)
    if total is None:
        diagnostics.preview(
            f"join: up to {per_item} model calls per input line (cap with --max-calls)"
        )
        return
    estimate = total * per_item
    if estimate > _PREVIEW_THRESHOLD:
        diagnostics.preview(
            f"join: {total:,} left items · up to {per_item} candidates each = "
            f"at most {estimate:,} model calls (cap with --max-calls)"
        )
