"""DEFER-3: finite (file) corpora embed in ≤64-text chunks.

The pinned contract (plan/post-1.0/06): 64x fewer round-trips on batch inputs;
streams stay per-item; a failed chunk re-runs item-by-item so one poison item
skips alone; output order and NDJSON shape are byte-identical to per-item runs.
"""

from __future__ import annotations

import asyncio
import io
import json
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from smartpipe.core.errors import (
    CircuitOpenTransport,
    ExcludedError,
    ExitCode,
    ItemError,
    LateSetupFault,
    RetryableError,
    SourceCounts,
    TooManyFailures,
)
from smartpipe.engine.runner import Done, FailurePolicy, Skipped
from smartpipe.io import manifest
from smartpipe.io.inputs import InputSpec
from smartpipe.io.items import item_from_line
from smartpipe.models.base import ChatModel, CompletionRequest, ImageData, ModelRef
from smartpipe.models.budget import CallBudget, budgeted_embed
from smartpipe.verbs.common import batched, embed_in_batches
from smartpipe.verbs.convert import Converter
from smartpipe.verbs.embed import EmbedRequest, run_embed

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from smartpipe.engine.runner import ItemOutcome
    from smartpipe.io.items import Item
    from smartpipe.models.base import EmbeddingModel


class BatchFake:
    """Deterministic embeddings; optionally poisoned or down entirely."""

    def __init__(self, *, poison: str | None = None, always_fail: bool = False) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.poison = poison
        self.always_fail = always_fail
        self.calls: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.calls.append(list(texts))
        if self.always_fail:
            raise ItemError("service down")
        if self.poison is not None and any(self.poison in text for text in texts):
            raise ItemError("poison batch")
        return tuple((float(len(text)), 1.0) for text in texts)


class FakeContext:
    def __init__(self, model: EmbeddingModel) -> None:
        self.model = model

    async def embedding_model(self, flag: str | None = None) -> EmbeddingModel:
        return self.model

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        from smartpipe.core.errors import SetupFault

        raise SetupFault("no chat configured — the converter takes the lower rungs")

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def failure_policy(self, provider: str) -> FailurePolicy:
        return FailurePolicy(transport_limit=5, transport_screen=f"{provider} unavailable")

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def document_parser(self, flag: str | None = None) -> None:
        return None

    async def media_embedding_model(self, flag: str | None = None) -> None:
        return None


class _TtyStdin(io.StringIO):
    """Pure --in runs read no stdin; a TTY marks 'files only, no pipe to chain'."""

    def isatty(self) -> bool:
        return True


def _corpus(tmp_path: Path, texts: Sequence[str]) -> InputSpec:
    for index, text in enumerate(texts):
        (tmp_path / f"{index:04}.txt").write_text(text, encoding="utf-8")
    return InputSpec(patterns=(str(tmp_path / "*.txt"),), from_files=False)


async def _run_files(
    tmp_path: Path,
    texts: Sequence[str],
    model: EmbeddingModel,
    *,
    stop: asyncio.Event | None = None,
) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_embed(
        EmbedRequest(model_flag=None, concurrency_flag=None, input=_corpus(tmp_path, texts)),
        FakeContext(model),
        stdin=_TtyStdin(),
        stdout=out,
        stop=stop,
    )
    return code, out.getvalue()


# --- call count + order -----------------------------------------------------------


async def test_130_items_take_exactly_three_calls(tmp_path: Path) -> None:
    texts = [f"text number {index}" for index in range(130)]
    model = BatchFake()
    code, out = await _run_files(tmp_path, texts, model)
    assert code is ExitCode.OK
    assert [len(call) for call in model.calls] == [64, 64, 2]
    emitted = [json.loads(line)["text"] for line in out.splitlines()]
    assert emitted == texts  # order preserved across chunk seams


async def test_finite_batches_use_bounded_api_call_concurrency_in_order() -> None:
    class ConcurrentBatch(BatchFake):
        def __init__(self) -> None:
            super().__init__()
            self.active = 0
            self.peak = 0
            self.two_started = asyncio.Event()
            self.release = asyncio.Event()

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(texts))
            self.active += 1
            self.peak = max(self.peak, self.active)
            if self.active == 2:
                self.two_started.set()
            await self.release.wait()
            self.active -= 1
            return tuple((float(len(text)), 1.0) for text in texts)

    model = ConcurrentBatch()

    async def collect() -> list[ItemOutcome[tuple[Item, tuple[float, ...]]]]:
        return [
            outcome
            async for outcome in embed_in_batches(
                model,
                _items([f"item {index}" for index in range(6)]),
                failure_policy=FailurePolicy(),
                batch_size=2,
                call_concurrency=2,
            )
        ]

    collecting = asyncio.create_task(collect())
    try:
        await asyncio.wait_for(model.two_started.wait(), timeout=1)
    finally:
        model.release.set()
    outcomes = await collecting

    assert model.peak == 2
    assert model.calls == [
        ["item 0", "item 1"],
        ["item 2", "item 3"],
        ["item 4", "item 5"],
    ]
    assert [outcome.index for outcome in outcomes] == list(range(6))


async def test_fatal_batch_failure_cancels_concurrent_siblings() -> None:
    class FatalBatch(BatchFake):
        def __init__(self) -> None:
            super().__init__()
            self.started = 0
            self.both_started = asyncio.Event()
            self.sibling_cancelled = asyncio.Event()

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(texts))
            self.started += 1
            if self.started == 2:
                self.both_started.set()
            await self.both_started.wait()
            if texts[0] == "item 0":
                raise CircuitOpenTransport("provider down", trip_id=1, call_id=2)
            try:
                await asyncio.Event().wait()
            except asyncio.CancelledError:
                self.sibling_cancelled.set()
                raise
            raise AssertionError("unreachable")  # pragma: no cover

    model = FatalBatch()
    with pytest.raises(LateSetupFault, match="embedding provider unavailable") as caught:
        _ = [
            outcome
            async for outcome in embed_in_batches(
                model,
                _items([f"item {index}" for index in range(4)]),
                failure_policy=FailurePolicy(
                    transport_limit=5,
                    transport_screen="embedding provider unavailable",
                ),
                batch_size=2,
                call_concurrency=2,
            )
        ]

    assert model.sibling_cancelled.is_set()
    assert caught.value.source_counts == SourceCounts(succeeded=0, skipped=4, failed=2)


async def test_fatal_batch_counts_already_failed_concurrent_calls() -> None:
    class ConcurrentFailures(BatchFake):
        def __init__(self) -> None:
            super().__init__()
            self.sibling_failed = asyncio.Event()

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(texts))
            if texts[0] == "item 0":
                await self.sibling_failed.wait()
                raise CircuitOpenTransport("provider down", trip_id=1, call_id=2)
            self.sibling_failed.set()
            raise RetryableError("provider down", series_id=1, call_id=3)

    model = ConcurrentFailures()
    with pytest.raises(LateSetupFault) as caught:
        _ = [
            outcome
            async for outcome in embed_in_batches(
                model,
                _items([f"item {index}" for index in range(4)]),
                failure_policy=FailurePolicy(
                    transport_limit=5,
                    transport_screen="embedding provider unavailable",
                ),
                batch_size=2,
                call_concurrency=2,
            )
        ]

    assert caught.value.source_counts == SourceCounts(succeeded=0, skipped=4, failed=4)


async def test_budget_stop_emits_every_remaining_admitted_item_in_order() -> None:
    items = _items([f"text number {index}" for index in range(130)])
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    inner = BatchFake()
    model = budgeted_embed(inner, budget)

    outcomes = [
        outcome
        async for outcome in embed_in_batches(
            model,
            items,
            failure_policy=FailurePolicy(),
            batch_size=64,
            stop=stop,
        )
    ]

    assert [len(call) for call in inner.calls] == [64]
    assert [outcome.index for outcome in outcomes] == list(range(130))
    assert all(isinstance(outcome, Done) for outcome in outcomes[:64])
    unsent = [outcome for outcome in outcomes[64:] if isinstance(outcome, Skipped)]
    assert len(unsent) == 66
    assert all(not outcome.failed for outcome in unsent)
    assert {outcome.reason for outcome in unsent} == {"run stopping — not sent"}


async def test_budget_stop_manifest_counts_unsent_rows_without_failures(tmp_path: Path) -> None:
    texts = [f"text number {index}" for index in range(130)]
    stop = asyncio.Event()
    budget = CallBudget(limit=1, stop=stop)
    inner = BatchFake()
    model = budgeted_embed(inner, budget)
    target = tmp_path / "manifest.json"
    manifest.reset()
    manifest.begin(target, verb="embed", argv=("embed", "--max-calls", "1"))

    code, out = await _run_files(tmp_path, texts, model, stop=stop)
    manifest.finish(code)

    document = json.loads(target.read_text(encoding="utf-8"))
    assert code is ExitCode.PARTIAL
    assert [len(call) for call in inner.calls] == [64]
    assert len(out.splitlines()) == 64
    assert document["items"] == {
        "in": 130,
        "succeeded": 64,
        "skipped": 66,
        "failed": 0,
    }


async def test_ocr_corpus_still_batches_via_the_two_pass_count(tmp_path: Path) -> None:
    """Item 49(b): total is unknown pre-parse, but a files-only OCR corpus is
    finite — parse first, then batch, instead of one embed call per item."""
    from tests.io.test_ocr_ingest import FakeParser

    parser = FakeParser(pages=3)

    class OcrContext(FakeContext):
        def document_parser(self, flag: str | None = None) -> FakeParser:  # type: ignore[override]
            return parser

    (tmp_path / "scan.pdf").write_bytes(b"%PDF-1.4 tiny")
    (tmp_path / "note.txt").write_text("plain text", encoding="utf-8")
    model = BatchFake()
    out = io.StringIO()
    code = await run_embed(
        EmbedRequest(
            model_flag=None,
            concurrency_flag=None,
            input=InputSpec(patterns=(str(tmp_path / "*"),), from_files=False),
        ),
        OcrContext(model),
        stdin=_TtyStdin(),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert [len(call) for call in model.calls] == [4]  # 3 pages + 1 text, ONE call
    emitted = [json.loads(line)["text"] for line in out.getvalue().splitlines()]
    assert emitted == ["plain text", "page 1 md", "page 2 md", "page 3 md"]


async def test_streamed_stdin_stays_per_item() -> None:
    # a live stream must not buffer 64 lines for throughput — latency wins
    model = BatchFake()
    out = io.StringIO()
    code = await run_embed(
        EmbedRequest(model_flag=None, concurrency_flag=None),
        FakeContext(model),
        stdin=io.StringIO("a\nbb\nccc\n"),
        stdout=out,
    )
    assert code is ExitCode.OK
    assert [len(call) for call in model.calls] == [1, 1, 1]


# --- poison isolation ---------------------------------------------------------------


async def test_poison_item_skips_alone(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    texts = [f"text {index}" for index in range(130)]
    texts[10] = "a bad one"
    model = BatchFake(poison="bad")
    code, out = await _run_files(tmp_path, texts, model)
    assert code is ExitCode.PARTIAL  # exit 1: one skip, neighbors emitted
    emitted = [json.loads(line)["text"] for line in out.splitlines()]
    assert emitted == [text for text in texts if "bad" not in text]
    assert "skipped" in capsys.readouterr().err
    # chunks 1-2 start concurrently; chunk 1 then isolates its 64 items in its
    # freed call slot, while chunk 3 stays batched.
    assert [len(call) for call in model.calls] == [64, 64, *([1] * 64), 2]


async def test_budget_stop_settles_poison_fallback_remainder_and_later_chunks() -> None:
    items = _items(["bad", *(f"text {index}" for index in range(1, 70))])
    stop = asyncio.Event()
    budget = CallBudget(limit=2, stop=stop)
    inner = BatchFake(poison="bad")

    outcomes = [
        outcome
        async for outcome in embed_in_batches(
            budgeted_embed(inner, budget),
            items,
            failure_policy=FailurePolicy(),
            batch_size=64,
            stop=stop,
        )
    ]

    assert [len(call) for call in inner.calls] == [64, 1]
    assert [outcome.index for outcome in outcomes] == list(range(70))
    first = outcomes[0]
    assert isinstance(first, Skipped) and first.failed
    unsent = [outcome for outcome in outcomes[1:] if isinstance(outcome, Skipped)]
    assert len(unsent) == 69
    assert all(not outcome.failed for outcome in unsent)


async def test_excluded_items_do_not_become_failures_in_finite_embedding() -> None:
    class ExcludingFake(BatchFake):
        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(texts))
            raise ExcludedError("excluded before primary model submission")

    model = ExcludingFake()
    outcomes = [
        outcome
        async for outcome in embed_in_batches(
            model,
            _items(["first", "second"]),
            failure_policy=FailurePolicy(),
            batch_size=2,
        )
    ]

    assert [len(call) for call in model.calls] == [2, 1, 1]
    assert all(isinstance(outcome, Skipped) for outcome in outcomes)
    assert all(not outcome.failed for outcome in outcomes if isinstance(outcome, Skipped))


async def test_retryable_batch_failure_fans_out_once_without_solo_amplification() -> None:
    class UnavailableBatch(BatchFake):
        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(texts))
            raise RetryableError("rate limit exhausted", series_id=11, call_id=23)

    model = UnavailableBatch()
    outcomes = [
        outcome
        async for outcome in embed_in_batches(
            model,
            _items([f"item {index}" for index in range(6)]),
            failure_policy=FailurePolicy(
                transport_limit=5,
                transport_screen="embedding provider unavailable",
            ),
            batch_size=6,
        )
    ]

    assert [len(call) for call in model.calls] == [6]
    assert len(outcomes) == 6
    assert all(isinstance(outcome, Skipped) for outcome in outcomes)
    assert {
        (outcome.transport, outcome.transport_series, outcome.transport_call)
        for outcome in outcomes
        if isinstance(outcome, Skipped)
    } == {(True, 11, 23)}


async def test_circuit_open_batch_uses_the_provider_down_setup_screen() -> None:
    class OpenCircuit(BatchFake):
        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(texts))
            raise CircuitOpenTransport("rate limit exhausted", trip_id=11, call_id=23)

    model = OpenCircuit()
    with pytest.raises(LateSetupFault, match="embedding provider unavailable") as caught:
        _ = [
            outcome
            async for outcome in embed_in_batches(
                model,
                _items([f"item {index}" for index in range(6)]),
                failure_policy=FailurePolicy(
                    transport_limit=5,
                    transport_screen="embedding provider unavailable",
                ),
                batch_size=6,
            )
        ]

    assert [len(call) for call in model.calls] == [6]
    assert caught.value.source_counts == SourceCounts(succeeded=0, skipped=6, failed=6)


async def test_retryable_solo_during_poison_isolation_leaves_the_remainder_unsent() -> None:
    class PoisonThenUnavailable(BatchFake):
        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(texts))
            if len(texts) > 1:
                raise ItemError("poison batch")
            raise RetryableError("provider unavailable", series_id=7, call_id=8)

    model = PoisonThenUnavailable()
    outcomes = [
        outcome
        async for outcome in embed_in_batches(
            model,
            _items(["first", "second", "third"]),
            failure_policy=FailurePolicy(transport_limit=5),
            batch_size=3,
        )
    ]

    assert [len(call) for call in model.calls] == [3, 1]
    first, *remainder = outcomes
    assert isinstance(first, Skipped) and first.failed and first.transport
    assert all(isinstance(outcome, Skipped) and not outcome.failed for outcome in remainder)


async def test_retryable_media_conversion_stays_an_availability_outcome() -> None:
    from dataclasses import replace

    from smartpipe.io import diagnostics

    class UnavailableVision:
        ref = ModelRef("ollama", "vision")

        async def complete(self, request: CompletionRequest) -> str:
            del request
            raise RetryableError("vision unavailable", series_id=3, call_id=4)

    converter = Converter(
        chat=UnavailableVision(),
        allow_paid=True,
        log=diagnostics.DegradationLog(),
    )
    item = replace(
        _items([""])[0],
        media=(ImageData(b"pixels", "image/png"),),
    )
    model = BatchFake()

    outcomes = [
        outcome
        async for outcome in embed_in_batches(
            model,
            [item],
            failure_policy=FailurePolicy(transport_limit=5),
            converter=converter,
        )
    ]

    assert model.calls == []
    assert len(outcomes) == 1
    skipped = outcomes[0]
    assert isinstance(skipped, Skipped)
    assert (skipped.transport, skipped.transport_series, skipped.transport_call) == (
        True,
        3,
        4,
    )


async def test_circuit_open_media_conversion_settles_the_finite_corpus() -> None:
    from dataclasses import replace

    from smartpipe.io import diagnostics

    class OpenVisionCircuit:
        ref = ModelRef("ollama", "vision")

        async def complete(self, request: CompletionRequest) -> str:
            del request
            raise CircuitOpenTransport("vision unavailable", trip_id=3, call_id=4)

    converter = Converter(
        chat=OpenVisionCircuit(),
        allow_paid=True,
        log=diagnostics.DegradationLog(),
    )
    item = replace(
        _items([""])[0],
        media=(ImageData(b"pixels", "image/png"),),
    )

    with pytest.raises(LateSetupFault, match="embedding provider unavailable") as caught:
        _ = [
            outcome
            async for outcome in embed_in_batches(
                BatchFake(),
                [item],
                failure_policy=FailurePolicy(
                    transport_limit=5,
                    transport_screen="embedding provider unavailable",
                ),
                converter=converter,
            )
        ]

    assert caught.value.source_counts == SourceCounts(succeeded=0, skipped=1, failed=1)


# --- whole-service failure ------------------------------------------------------------


async def test_small_doomed_corpus_halts_after_five(tmp_path: Path) -> None:
    # D18: zero successes + 5 consecutive failures = the run was doomed from item 1.
    # Before the guardrail this ground through all 10 items to ALL_FAILED.
    texts = [f"text {index}" for index in range(10)]
    model = BatchFake(always_fail=True)
    with pytest.raises(TooManyFailures) as excinfo:
        await _run_files(tmp_path, texts, model)
    assert excinfo.value.source_counts == SourceCounts(succeeded=0, skipped=10, failed=5)
    assert len(model.calls) == 6  # 1 failed chunk + 5 per-item fallbacks, then stop


async def test_doomed_ocr_page_batch_halts_with_one_source() -> None:
    from dataclasses import replace

    from smartpipe.io import source_accounting

    source_accounting.reset()
    group = source_accounting.new_group(size=3)
    items = [
        replace(item, source=replace(item.source, cut="pages", group=group))
        for item in _items(["page one", "page two", "page three"])
    ]
    model = BatchFake(always_fail=True)

    with pytest.raises(TooManyFailures) as caught:
        _ = [
            outcome
            async for outcome in embed_in_batches(
                model,
                items,
                failure_policy=FailurePolicy(min_sample=10**9, consecutive_limit=2),
                batch_size=3,
            )
        ]

    assert caught.value.source_counts == SourceCounts(succeeded=0, skipped=1, failed=1)


async def test_tiny_corpus_below_the_consecutive_limit_still_reports_all_failed(
    tmp_path: Path,
) -> None:
    texts = [f"text {index}" for index in range(3)]  # fewer than 5 — no halt to trip
    model = BatchFake(always_fail=True)
    code, out = await _run_files(tmp_path, texts, model)
    assert code is ExitCode.ALL_FAILED
    assert out == ""


async def test_majority_failure_halts_instead_of_grinding(tmp_path: Path) -> None:
    texts = [f"text {index}" for index in range(130)]
    model = BatchFake(always_fail=True)
    with pytest.raises(TooManyFailures):
        await _run_files(tmp_path, texts, model)
    # the circuit breaker tripped long before 130 per-item fallback calls
    assert len(model.calls) < 30


# --- batched == per-item (property) ---------------------------------------------------


def _items(texts: Sequence[str]) -> list[Item]:
    return [item_from_line(f"{text}\n", index) for index, text in enumerate(texts)]


@given(
    texts=st.lists(
        st.text(alphabet=st.characters(blacklist_characters="\r\n"), max_size=8),
        max_size=150,
    ),
    batch_size=st.integers(min_value=1, max_value=70),
)
@settings(max_examples=30, deadline=None)
def test_batched_pipeline_equals_per_item(texts: list[str], batch_size: int) -> None:
    async def collect() -> list[tuple[int, tuple[float, ...]]]:
        model = BatchFake()
        pairs: list[tuple[int, tuple[float, ...]]] = []
        outcomes = embed_in_batches(
            model, _items(texts), failure_policy=FailurePolicy(), batch_size=batch_size
        )
        async for outcome in outcomes:
            assert isinstance(outcome, Done)
            _item, vector = outcome.value
            pairs.append((outcome.index, vector))
        return pairs

    async def per_item() -> list[tuple[int, tuple[float, ...]]]:
        model = BatchFake()
        return [
            (index, (await model.embed([item.text]))[0]) for index, item in enumerate(_items(texts))
        ]

    assert asyncio.run(collect()) == asyncio.run(per_item())


# --- the chunker itself ---------------------------------------------------------------


def test_batched_chunks_and_orders() -> None:
    assert list(batched("abcdefg", 3)) == [("a", "b", "c"), ("d", "e", "f"), ("g",)]
    empty: list[str] = []
    assert list(batched(empty, 3)) == []


def test_batched_rejects_a_zero_size() -> None:
    with pytest.raises(ValueError, match="batch size"):
        list(batched([1, 2], 0))


async def test_pre_set_stop_reports_interrupted(tmp_path: Path) -> None:
    import asyncio

    stop = asyncio.Event()
    stop.set()  # the drain fired before the batch began
    model = BatchFake()
    out = io.StringIO()
    from smartpipe.verbs.embed import EmbedRequest as _Req

    code = await run_embed(
        _Req(model_flag=None, concurrency_flag=None, input=_corpus(tmp_path, ["a", "b"])),
        FakeContext(model),
        stdin=_TtyStdin(),
        stdout=out,
        stop=stop,
    )
    assert code is ExitCode.INTERRUPTED  # nothing finished at all (ux.md §12)
    assert out.getvalue() == ""


async def test_oversized_item_is_pooled_from_chunk_vectors(tmp_path: Path) -> None:
    class PoolFake:
        def __init__(self) -> None:
            self.ref = ModelRef("openai", "text-embedding-3-small")
            self.calls: list[list[str]] = []

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            self.calls.append(list(texts))
            # distinct constant vectors so the mean is recognizable
            return tuple((float(i + 1), 0.0) for i in range(len(texts)))

    model = PoolFake()
    small = "tiny"
    big = "x" * 50_000  # ~12.5k tokens, past the 4.8k embed budget
    items = _items([small, big, small])
    outcomes = [o async for o in embed_in_batches(model, items, failure_policy=FailurePolicy())]
    assert [type(o).__name__ for o in outcomes] == ["Done", "Done", "Done"]
    assert [o.index for o in outcomes] == [0, 1, 2]  # stdout order = input order
    # the big item's vector is the mean of its chunk vectors, not a single call
    big_call = max(model.calls, key=len)
    assert len(big_call) >= 3  # it really was chunked
    done_big = outcomes[1]
    assert isinstance(done_big, Done)
    _item_out, vector = done_big.value
    expected_first = sum(range(1, len(big_call) + 1)) / len(big_call)
    assert vector == (expected_first, 0.0)
