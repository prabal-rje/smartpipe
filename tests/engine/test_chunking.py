from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sempipe.engine.chunking import (
    budget_for,
    chunk_indices,
    estimate_tokens,
    fits_in_one,
    halve,
    is_context_overflow,
    mean_pool,
    split_text,
)

# --- estimation ---------------------------------------------------------------


def test_estimate_tokens() -> None:
    assert estimate_tokens("") == 0
    assert estimate_tokens("abcd") == 1  # 4 chars ≈ 1 token
    assert estimate_tokens("a" * 10) == 3  # ceil(10/4)


# --- budget -------------------------------------------------------------------


def test_budget_applies_safety_factor_and_overhead() -> None:
    # ollama default 8000 * 0.6 = 4800, minus 200 overhead
    assert budget_for("ollama", prompt_overhead=200) == 4600


def test_budget_by_provider() -> None:
    assert budget_for("anthropic", prompt_overhead=0) == 120000  # 200000 * 0.6
    assert budget_for("openai", prompt_overhead=0) == 76800  # 128000 * 0.6


# --- chunking -----------------------------------------------------------------


def test_all_items_fit_in_one_chunk() -> None:
    assert chunk_indices([10, 20, 30], budget=100) == ((0, 1, 2),)


def test_splits_when_over_budget() -> None:
    # budget 50: [30, 30] exceeds → split; [30] + [30, 10] etc.
    assert chunk_indices([30, 30, 10], budget=50) == ((0,), (1, 2))


def test_oversize_single_item_gets_its_own_chunk() -> None:
    # item 1 alone (100) exceeds budget 50 — can't split, so it's alone
    assert chunk_indices([10, 100, 10], budget=50) == ((0,), (1,), (2,))


def test_fits_in_one() -> None:
    assert fits_in_one([10, 20], budget=100) is True
    assert fits_in_one([60, 60], budget=100) is False


# --- properties ---------------------------------------------------------------


@given(
    sizes=st.lists(st.integers(min_value=0, max_value=100), max_size=30),
    budget=st.integers(min_value=1, max_value=200),
)
def test_chunking_invariants(sizes: list[int], budget: int) -> None:
    chunks = chunk_indices(sizes, budget)
    # every item appears exactly once, in order
    flat = [i for chunk in chunks for i in chunk]
    assert flat == list(range(len(sizes)))
    # each chunk fits, unless it's a single item that alone exceeds the budget
    for chunk in chunks:
        total = sum(sizes[i] for i in chunk)
        assert total <= budget or len(chunk) == 1


@given(sizes=st.lists(st.integers(min_value=0, max_value=50), min_size=1, max_size=20))
def test_fits_implies_one_chunk(sizes: list[int]) -> None:
    budget = sum(sizes) + 1
    assert fits_in_one(sizes, budget)
    assert len(chunk_indices(sizes, budget)) == 1


def test_window_override_beats_the_table() -> None:
    assert budget_for("ollama", prompt_overhead=0, window=100_000) == 60_000
    assert budget_for("openai", prompt_overhead=500) == int(128_000 * 0.6) - 500


@pytest.mark.parametrize(
    "message",
    [
        "This model's maximum context length is 8192 tokens",  # openai
        "prompt is too long: 210000 tokens > 200000 maximum",  # anthropic
        "the input length exceeds the limit",
        "Request too large",  # mistral 413-style
    ],
)
def test_overflow_messages_classify(message: str) -> None:
    assert is_context_overflow(message) is True


def test_ordinary_errors_do_not_classify() -> None:
    assert is_context_overflow("model returned invalid JSON") is False
    assert is_context_overflow("rate limited, retry later") is False


def test_halve_splits_non_empty() -> None:
    assert halve((0, 1, 2, 3, 4)) == ((0, 1), (2, 3, 4))
    assert halve((7, 9)) == ((7,), (9,))


# --- split_text (D26 layer 3) --------------------------------------------------


def test_small_text_is_one_chunk() -> None:
    assert split_text("hello", 100) == ("hello",)


def test_paragraph_boundaries_win() -> None:
    text = "para one is here.\n\npara two is here.\n\npara three."
    chunks = split_text(text, budget=6)  # ~24 chars per chunk
    assert all(estimate_tokens(c) <= 6 or "\n\n" not in c for c in chunks)
    assert "".join(chunks) == text  # nothing added, nothing lost


@given(
    body=st.text(min_size=0, max_size=2000),
    budget=st.integers(min_value=1, max_value=50),
)
def test_chunks_always_reassemble_exactly(body: str, budget: int) -> None:
    assert "".join(split_text(body, budget)) == body


@given(
    body=st.text(alphabet="ab \n", min_size=1, max_size=800),
    budget=st.integers(min_value=2, max_value=20),
)
def test_multi_piece_chunks_respect_the_budget(body: str, budget: int) -> None:
    # any chunk over budget must be a single indivisible hard-cut piece
    for chunk in split_text(body, budget):
        assert estimate_tokens(chunk) <= budget or len(chunk) <= budget * 4 + 2


def test_mean_pool_averages_componentwise() -> None:
    assert mean_pool([(1.0, 0.0), (0.0, 1.0)]) == (0.5, 0.5)
    assert mean_pool([(2.0, 4.0)]) == (2.0, 4.0)
