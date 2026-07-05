"""DEFER-3: finite (file) corpora embed in ≤64-text chunks, sequentially.

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

from sempipe.core.errors import ExitCode, ItemError, TooManyFailures
from sempipe.engine.runner import Done, FailurePolicy
from sempipe.io.inputs import InputSpec
from sempipe.io.items import item_from_line
from sempipe.models.base import ModelRef
from sempipe.verbs.common import batched, embed_in_batches
from sempipe.verbs.embed import EmbedRequest, run_embed

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

    from sempipe.io.items import Item


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
    def __init__(self, model: BatchFake) -> None:
        self.model = model

    async def embedding_model(self, flag: str | None = None) -> BatchFake:
        return self.model

    def concurrency(self, flag: int | None = None) -> int:
        return 2


class _TtyStdin(io.StringIO):
    """Pure --in runs read no stdin; a TTY marks 'files only, no pipe to chain'."""

    def isatty(self) -> bool:
        return True


def _corpus(tmp_path: Path, texts: Sequence[str]) -> InputSpec:
    for index, text in enumerate(texts):
        (tmp_path / f"{index:04}.txt").write_text(text, encoding="utf-8")
    return InputSpec(patterns=(str(tmp_path / "*.txt"),), from_files=False)


async def _run_files(
    tmp_path: Path, texts: Sequence[str], model: BatchFake
) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_embed(
        EmbedRequest(model_flag=None, concurrency_flag=None, input=_corpus(tmp_path, texts)),
        FakeContext(model),
        stdin=_TtyStdin(),
        stdout=out,
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
    # chunk 1 failed once, re-ran per item (64 singles); chunks 2-3 stayed batched
    assert [len(call) for call in model.calls] == [64, *([1] * 64), 64, 2]


# --- whole-service failure ------------------------------------------------------------


async def test_small_doomed_corpus_halts_after_five(tmp_path: Path) -> None:
    # D18: zero successes + 5 consecutive failures = the run was doomed from item 1.
    # Before the guardrail this ground through all 10 items to ALL_FAILED.
    texts = [f"text {index}" for index in range(10)]
    model = BatchFake(always_fail=True)
    with pytest.raises(TooManyFailures):
        await _run_files(tmp_path, texts, model)
    assert len(model.calls) == 6  # 1 failed chunk + 5 per-item fallbacks, then stop


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
