"""The paced-server helper's own contracts (the parts tests lean on blindly)."""

from __future__ import annotations

import pytest

from tests.helpers.paced import PacedOllama


def test_default_embed_table_overflow_raises_a_clear_error() -> None:
    """Review NIT: the default one-hot generator has a fixed width; a corpus
    with more distinct inputs must fail with a legible message, not an
    IndexError deep inside the HTTP handler."""
    server = PacedOllama(lambda _body: "", paced=False)  # never started: pure reply math
    body: dict[str, object] = {"input": [f"name-{n}" for n in range(257)]}
    with pytest.raises(AssertionError, match="embed_reply"):
        server._embed_rows(body)  # pyright: ignore[reportPrivateUsage] — the seam under test


def test_default_embed_rows_match_the_input_count_with_stable_slots() -> None:
    server = PacedOllama(lambda _body: "", paced=False)
    first = server._embed_rows({"input": ["a", "b"]})  # pyright: ignore[reportPrivateUsage]
    second = server._embed_rows({"input": ["b", "c"]})  # pyright: ignore[reportPrivateUsage]
    assert len(first) == 2
    assert len(second) == 2
    assert second[0] == first[1]  # "b" keeps its slot across batches
    assert first[0] != first[1]  # distinct inputs never collide
