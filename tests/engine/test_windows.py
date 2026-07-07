from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from smartpipe.engine.windows import Window, WindowBuffer, WindowPolicy

# --- policy validation ----------------------------------------------------------


def test_policy_validates() -> None:
    WindowPolicy(size=1, every=1)  # minimal is fine
    with pytest.raises(ValueError):
        WindowPolicy(size=0, every=1)
    with pytest.raises(ValueError):
        WindowPolicy(size=3, every=0)
    with pytest.raises(ValueError):
        WindowPolicy(size=3, every=4)  # every > size would skip items


def _drive(policy: WindowPolicy, n: int) -> tuple[list[Window[int]], Window[int] | None]:
    buffer: WindowBuffer[int] = WindowBuffer(policy)
    emitted: list[Window[int]] = []
    for value in range(1, n + 1):
        window = buffer.push(value)
        if window is not None:
            emitted.append(window)
    return emitted, buffer.flush()


# --- golden sequences (pinned in the plan) ---------------------------------------


def test_tumbling_n3_over_seven() -> None:
    emitted, partial = _drive(WindowPolicy(size=3, every=3), 7)
    assert [w.items for w in emitted] == [(1, 2, 3), (4, 5, 6)]
    assert [w.end_index for w in emitted] == [3, 6]
    assert all(not w.partial for w in emitted)
    assert partial is not None
    assert partial.items == (7,) and partial.end_index == 7 and partial.partial


def test_sliding_n3_m1_over_five() -> None:
    emitted, partial = _drive(WindowPolicy(size=3, every=1), 5)
    assert [w.items for w in emitted] == [(1, 2, 3), (2, 3, 4), (3, 4, 5)]
    assert [w.end_index for w in emitted] == [3, 4, 5]
    assert partial is None  # nothing arrived after the last emission


def test_stream_shorter_than_window_is_one_partial() -> None:
    emitted, partial = _drive(WindowPolicy(size=5, every=5), 2)
    assert emitted == []
    assert partial is not None
    assert partial.items == (1, 2) and partial.partial


def test_empty_stream_flushes_nothing() -> None:
    emitted, partial = _drive(WindowPolicy(size=3, every=3), 0)
    assert emitted == [] and partial is None


# --- properties -------------------------------------------------------------------


@given(n=st.integers(min_value=0, max_value=60), size=st.integers(min_value=1, max_value=9))
def test_tumbling_equals_chunking(n: int, size: int) -> None:
    emitted, partial = _drive(WindowPolicy(size=size, every=size), n)
    chunks = [tuple(range(i + 1, min(i + size, n) + 1)) for i in range(0, n, size)]
    full = [c for c in chunks if len(c) == size]
    assert [w.items for w in emitted] == full
    leftover = chunks[-1] if chunks and len(chunks[-1]) < size else None
    assert (partial.items if partial else None) == leftover


@given(
    n=st.integers(min_value=0, max_value=60),
    size=st.integers(min_value=1, max_value=9),
    data=st.data(),
)
def test_window_invariants(n: int, size: int, data: st.DataObject) -> None:
    every = data.draw(st.integers(min_value=1, max_value=size))
    emitted, partial = _drive(WindowPolicy(size=size, every=every), n)
    # emitted windows are exactly `size` long; the flush (if any) is shorter
    assert all(len(w.items) == size and not w.partial for w in emitted)
    if partial is not None:
        assert 1 <= len(partial.items) <= size and partial.partial
    # end_index strictly increases across everything emitted
    ends = [w.end_index for w in emitted] + ([partial.end_index] if partial else [])
    assert ends == sorted(set(ends))
    # coverage: with every <= size, each value appears in at least one window
    seen: set[int] = {v for w in emitted for v in w.items} | (
        set(partial.items) if partial else set()
    )
    assert seen == set(range(1, n + 1))
