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
from dataclasses import dataclass, replace
from typing import TYPE_CHECKING, Protocol

from sempipe.core.errors import ExitCode, ItemError, TooManyFailures, UsageFault
from sempipe.engine.blocking import RightIndex, build_index, candidates
from sempipe.engine.prompts import (
    JUDGE_SCHEMA,
    build_judge_request,
    build_repair_request,
    parse_join_predicate,
)
from sempipe.engine.runner import (
    Done,
    FailurePolicy,
    run_ordered,
    should_halt,
    should_halt_consecutive,
)
from sempipe.engine.schema import validate_and_coerce
from sempipe.io import diagnostics, readers
from sempipe.io.inputs import STDIN
from sempipe.io.items import ItemSource, describe_source, item_from_line
from sempipe.io.progress import make_stderr_spinner
from sempipe.verbs.common import (
    embed_in_batches,
    ensure_text,
    interrupted_exit_code,
    outcome_exit_code,
)

if TYPE_CHECKING:
    from pathlib import Path
    from typing import TextIO

    from sempipe.engine.prompts import Token
    from sempipe.io.inputs import InputSpec
    from sempipe.io.items import Item
    from sempipe.io.writers import OutputFormat, ResultWriter
    from sempipe.models.base import ChatModel, EmbeddingModel

__all__ = ["JoinContext", "JoinRequest", "run_join"]

_PREVIEW_THRESHOLD = 200  # estimated judge calls before the cost line appears (D21)


@dataclass(frozen=True, slots=True)
class JoinRequest:
    predicate: str
    right: Path
    k: int
    threshold: float | None
    model_flag: str | None
    embed_model_flag: str | None
    concurrency_flag: int | None
    output: OutputFormat
    input: InputSpec = STDIN
    fields: tuple[str, ...] | None = None


class JoinContext(Protocol):
    """The first verb that needs BOTH models — the container already has both."""

    async def chat_model(self, flag: str | None = None) -> ChatModel: ...
    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel: ...
    def concurrency(self, flag: int | None = None) -> int: ...
    def writer(
        self,
        output_flag: OutputFormat,
        *,
        structured: bool,
        stdout: TextIO,
        fields: tuple[str, ...] | None = None,
    ) -> ResultWriter: ...


@dataclass(slots=True)
class _PairBook:
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
    tokens = parse_join_predicate(request.predicate)  # UsageFault on bad grammar
    if request.k < 1:
        raise UsageFault(f"--k must be >= 1, got {request.k}")
    right_items = _load_right(request.right)
    embed_model = await context.embedding_model(request.embed_model_flag)
    kept_right, index = await _index_right(embed_model, right_items, request.right.name)
    chat = await context.chat_model(request.model_flag)
    concurrency = context.concurrency(request.concurrency_flag)
    writer = context.writer(request.output, structured=True, stdout=stdout, fields=request.fields)
    items_iter, total = readers.resolve_items(request.input, stdin, stop=stop)
    _preview(total, request.k, len(index))

    book = _PairBook(policy=FailurePolicy(), right_name=request.right.name)
    spinner = make_stderr_spinner()
    spinner.start(total=total)

    async def worker(item: Item) -> tuple[Item, tuple[tuple[int, float], ...]]:
        matches = await _join_one(
            item,
            embed_model=embed_model,
            chat=chat,
            tokens=tokens,
            index=index,
            kept_right=kept_right,
            request=request,
            book=book,
            stop=stop,
        )
        return item, matches

    done = 0
    skipped = 0
    outcomes = run_ordered(
        items_iter, worker, concurrency=concurrency, failure_policy=FailurePolicy(), stop=stop
    )
    try:
        async for outcome in outcomes:
            if isinstance(outcome, Done):
                left, matches = outcome.value
                for position, score in matches:
                    writer.write_record(
                        {
                            "left": _payload(left),
                            "right": _payload(kept_right[position]),
                            "_score": round(score, 4),
                        }
                    )
                done += 1
            else:  # Skipped — the left item itself failed (image, embed error, …)
                diagnostics.warn(f"skipped: {describe_source(outcome.source)} ({outcome.reason})")
                skipped += 1
            spinner.advance()
    finally:
        spinner.finish()
        writer.flush()
    if stop is not None and stop.is_set():
        diagnostics.interrupted_summary(processed=done, skipped=skipped + book.skipped)
        return interrupted_exit_code(done=done, skipped=skipped + book.skipped)
    return outcome_exit_code(done=done, skipped=skipped + book.skipped)


async def _join_one(
    item: Item,
    *,
    embed_model: EmbeddingModel,
    chat: ChatModel,
    tokens: tuple[Token, ...],
    index: RightIndex,
    kept_right: list[Item],
    request: JoinRequest,
    book: _PairBook,
    stop: asyncio.Event | None,
) -> tuple[tuple[int, float], ...]:
    item = await ensure_text(item)  # image skips; audio transcribes (D20 rung 2)
    vector = (await embed_model.embed([item.text]))[0]
    matches: list[tuple[int, float]] = []
    for position, score in candidates(vector, index, k=request.k, threshold=request.threshold):
        if stop is not None and stop.is_set():
            break
        right_item = kept_right[position]
        try:
            verdict = await _judge(chat, tokens, item, right_item)
        except ItemError as exc:
            book.skip(item, position, str(exc))
            continue
        book.ok()
        if verdict:
            matches.append((position, score))
    return tuple(matches)


def _payload(item: Item) -> dict[str, object]:
    return dict(item.data) if item.data is not None else {"text": item.text}


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


def _load_right(path: Path) -> list[Item]:
    if str(path) == "-":
        raise UsageFault(
            "--right - reads nothing — stdin is join's left side\n"
            "  The right side is a finite file sempipe indexes up front.\n"
            '  Example: cat stream.jsonl | sempipe join "…" --right catalog.jsonl'
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


async def _index_right(
    model: EmbeddingModel, items: list[Item], right_name: str
) -> tuple[list[Item], RightIndex]:
    """The build side, fully embedded before any chat spend (the preflight)."""
    kept: list[Item] = []
    vectors: list[tuple[float, ...]] = []
    async for outcome in embed_in_batches(model, items, failure_policy=FailurePolicy()):
        if isinstance(outcome, Done):
            item, vector = outcome.value
            kept.append(item)
            vectors.append(vector)
        else:
            diagnostics.warn(f"skipped: {right_name} line {outcome.index + 1} ({outcome.reason})")
    return kept, build_index(vectors)


def _preview(total: int | None, k: int, index_size: int) -> None:
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
