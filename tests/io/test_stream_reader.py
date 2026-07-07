"""The incremental stdin reader — each test pins one hazard (stage-08 as amended).

Real ``os.pipe`` file objects where blocking matters; synchronization by events and
bounded waits, never bare sleeps-as-sync.
"""

from __future__ import annotations

import asyncio
import io
import os
import threading
from typing import TYPE_CHECKING

import pytest

from smartpipe.io import readers
from smartpipe.io.readers import stdin_items

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import TextIO


class _Pipe:
    """A real OS pipe exposed as (reader TextIO, write(str), close_write())."""

    def __init__(self) -> None:
        r_fd, self._w_fd = os.pipe()
        self.reader: TextIO = os.fdopen(r_fd, "r", encoding="utf-8")
        self._open = True

    def write(self, text: str) -> None:
        os.write(self._w_fd, text.encode("utf-8"))

    def close_write(self) -> None:
        if self._open:
            os.close(self._w_fd)
            self._open = False

    def close(self) -> None:
        self.close_write()
        self.reader.close()


@pytest.fixture
def pipe() -> Iterator[_Pipe]:
    p = _Pipe()
    yield p
    p.close()


async def test_first_item_yields_before_eof(pipe: _Pipe) -> None:
    """THE streaming property: an item is available while the pipe is still open."""
    it = stdin_items(pipe.reader)
    pipe.write("alpha\n")
    item = await asyncio.wait_for(anext(it), timeout=2)
    assert item.text == "alpha"  # pipe still open — this was impossible pre-streaming
    pipe.close_write()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(it), timeout=2)


async def test_eof_ends_iteration(pipe: _Pipe) -> None:
    pipe.write("a\nb\n")
    pipe.close_write()
    items = [item.text async for item in stdin_items(pipe.reader)]
    assert items == ["a", "b"]


async def test_final_line_without_newline(pipe: _Pipe) -> None:
    pipe.write("a\ntail")
    pipe.close_write()
    items = [item.raw async for item in stdin_items(pipe.reader)]
    assert items == ["a", "tail"]


async def test_blank_and_crlf_lines(pipe: _Pipe) -> None:
    pipe.write("\nx\r\n")
    pipe.close_write()
    items = [item.raw async for item in stdin_items(pipe.reader)]
    assert items == ["", "x"]  # parity with item_from_line's CRLF rule


async def test_stop_ends_iteration_while_reader_is_blocked(pipe: _Pipe) -> None:
    """The shutdown hazard: stop must win even when readline is parked forever."""
    stop = asyncio.Event()
    it = stdin_items(pipe.reader, stop=stop)
    pull = asyncio.ensure_future(anext(it))
    await asyncio.sleep(0.05)  # let the pull park on the (empty, open) pipe
    stop.set()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pull, timeout=1)  # ends although the pipe never closed


async def test_no_leaked_tasks_after_stop(pipe: _Pipe) -> None:
    stop = asyncio.Event()
    it = stdin_items(pipe.reader, stop=stop)
    pull = asyncio.ensure_future(anext(it))
    await asyncio.sleep(0.05)
    stop.set()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(pull, timeout=1)
    await asyncio.sleep(0)  # let cancellations settle
    stray = [t for t in asyncio.all_tasks() if t is not asyncio.current_task() and not t.done()]
    assert stray == []


async def test_bounded_queue_backpressure(monkeypatch: pytest.MonkeyPatch) -> None:
    """With a tiny queue and no consumption, the pump must stall — not slurp stdin."""
    monkeypatch.setattr(readers, "_QUEUE_MAX", 4)
    reads = 0
    drained = threading.Event()

    class CountingStdin(io.StringIO):
        def readline(self, *args: object) -> str:  # type: ignore[override]
            nonlocal reads
            reads += 1
            line = super().readline()
            if not line:
                drained.set()
            return line

    stdin = CountingStdin("".join(f"line-{i}\n" for i in range(100)))
    it = stdin_items(stdin)
    first = await asyncio.wait_for(anext(it), timeout=2)
    assert first.text == "line-0"
    await asyncio.sleep(0.2)  # bounded grace, asserting an upper bound (not timing)
    # 1 consumed + 4 queued + 1 in flight + 1 lookahead — far below 100 if bounded
    assert reads <= 8
    rest = [item.text async for item in it]
    assert len(rest) == 99  # everything still arrives once consumption resumes
    assert drained.wait(timeout=2)


async def test_stringio_path_matches_pipe_path() -> None:
    """Equivalence: the same lines through StringIO and a real pipe yield identical items."""
    lines = ["a", "", 'json: {"k": 1}', "final"]
    text = "\n".join(lines) + "\n"

    async def collect(source: TextIO) -> list[tuple[str, int]]:
        return [(i.raw, i.source.index) async for i in stdin_items(source)]

    p = _Pipe()
    try:
        p.write(text)
        p.close_write()
        assert await collect(io.StringIO(text)) == await collect(p.reader)
    finally:
        p.close()
