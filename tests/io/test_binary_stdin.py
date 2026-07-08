"""Binary stdin (stage-07 task 4): the byte/text dispatch hazard table."""

from __future__ import annotations

import asyncio
import glob
import io
import os
import queue
import sys
import tempfile
import threading
import types
from typing import TYPE_CHECKING

import pytest
from hypothesis import given, settings
from hypothesis import strategies as st

from smartpipe.core.errors import SetupFault
from smartpipe.io.readers import stdin_items

if TYPE_CHECKING:
    from collections.abc import Iterator
    from typing import TextIO

PDF_FIXTURE = "tests/corpus/one-page.pdf"


def pathlib_read(path: str) -> bytes:
    from pathlib import Path

    return Path(path).read_bytes()


class _BytePipe:
    """A real OS pipe with writes pumped through ONE ordered background thread.

    Windows anonymous pipes buffer ~8 KiB (POSIX: 64 KiB); a straddle-sized
    os.write on the event-loop thread blocked forever there while the reader
    task could never run - the CI hang that burned a 6-hour runner. The pump
    thread may block harmlessly; the loop stays free to drain.
    """

    def __init__(self) -> None:
        r_fd, self.w_fd = os.pipe()
        self.reader: TextIO = os.fdopen(r_fd, "r", encoding="utf-8")
        self._open = True
        self._queue: queue.SimpleQueue[bytes | None] = queue.SimpleQueue()
        self._pump_thread = threading.Thread(target=self._pump, daemon=True)
        self._pump_thread.start()

    def _pump(self) -> None:
        while (chunk := self._queue.get()) is not None:
            os.write(self.w_fd, chunk)
        os.close(self.w_fd)

    def write(self, data: bytes) -> None:
        self._queue.put(data)

    def close_write(self) -> None:
        if self._open:
            self._open = False
            self._queue.put(None)  # the pump closes the fd → reader sees EOF

    def close(self) -> None:
        self.close_write()
        self._pump_thread.join(timeout=5)
        self.reader.close()


@pytest.fixture
def pipe() -> Iterator[_BytePipe]:
    p = _BytePipe()
    yield p
    p.close()


async def _collect(pipe: _BytePipe) -> list[object]:
    return [item async for item in stdin_items(pipe.reader)]


async def test_text_bytes_match_the_stringio_path(pipe: _BytePipe) -> None:
    pipe.write(b"a\nb\n")
    pipe.close_write()
    via_pipe = [(i.raw, i.source.index) async for i in stdin_items(pipe.reader)]
    via_stringio = [(i.raw, i.source.index) async for i in stdin_items(io.StringIO("a\nb\n"))]
    assert via_pipe == via_stringio == [("a", 0), ("b", 1)]


async def test_pdf_on_stdin_is_one_document_item(
    pipe: _BytePipe, monkeypatch: pytest.MonkeyPatch
) -> None:
    module = types.ModuleType("markitdown")

    class _Result:
        text_content = "EXTRACTED TEXT"

    class MarkItDown:
        def convert(self, path: str) -> _Result:
            assert path.endswith(".pdf")  # the spool keeps the suffix markitdown routes on
            return _Result()

    module.MarkItDown = MarkItDown  # type: ignore[attr-defined]
    monkeypatch.setitem(sys.modules, "markitdown", module)

    before = set(glob.glob(os.path.join(tempfile.gettempdir(), "tmp*")))
    pipe.write(pathlib_read(PDF_FIXTURE))
    pipe.close_write()
    items = [item async for item in stdin_items(pipe.reader)]
    assert len(items) == 1
    assert items[0].text == "EXTRACTED TEXT"
    assert items[0].source.kind == "file" and items[0].source.name == "<stdin>"
    after = set(glob.glob(os.path.join(tempfile.gettempdir(), "tmp*")))
    assert after - before == set()  # the spool never outlives the run


async def test_line_straddling_the_sniff_boundary_stays_whole(pipe: _BytePipe) -> None:
    long_line = b"x" * 8191  # the sniff reads up to 8192: this line straddles it
    pipe.write(long_line + b"\ny\n")
    pipe.close_write()
    items = [i.raw async for i in stdin_items(pipe.reader)]
    assert items == ["x" * 8191, "y"]  # the off-by-one trap


async def test_multibyte_char_split_at_the_boundary(pipe: _BytePipe) -> None:
    # one line of é (2 bytes each) sized so the sniff cuts a character in half
    body = ("é" * 4100).encode()  # 8200 bytes > 8192
    pipe.write(body + b"\n")
    pipe.close_write()
    items = [i.raw async for i in stdin_items(pipe.reader)]
    assert items == ["é" * 4100]  # decode happens per assembled line — no mojibake


async def test_binary_garbage_is_a_setup_fault(pipe: _BytePipe) -> None:
    pipe.write(b"\x00\x01\x02\xff\x87\x00\x00garbage")
    pipe.close_write()
    with pytest.raises(SetupFault, match="binary data smartpipe can't parse"):
        await _collect(pipe)


async def test_streaming_survives_the_sniff(pipe: _BytePipe) -> None:
    it = stdin_items(pipe.reader)
    pipe.write(b"alpha\n")  # sniff sees only this — must NOT wait for 8 KiB
    item = await asyncio.wait_for(anext(it), timeout=2)
    assert item.text == "alpha"  # arrived while the pipe is still open
    pipe.close_write()
    with pytest.raises(StopAsyncIteration):
        await asyncio.wait_for(anext(it), timeout=2)


async def test_image_on_stdin_becomes_an_image_item(pipe: _BytePipe) -> None:
    png = b"\x89PNG\r\n\x1a\n" + b"\x00" * 32
    pipe.write(png)
    pipe.close_write()
    items = [item async for item in stdin_items(pipe.reader)]
    assert len(items) == 1
    assert len(items[0].media) == 1 and items[0].media[0].mime == "image/png"
    assert items[0].media[0].data == png


@settings(max_examples=30, deadline=None)
@given(blob=st.binary(max_size=512))
def test_random_bytes_never_raise_untyped(blob: bytes) -> None:
    async def run() -> None:
        p = _BytePipe()
        try:
            p.write(blob)
            p.close_write()
            import contextlib

            with contextlib.suppress(SetupFault):  # the only permissible failure
                await asyncio.wait_for(_collect(p), timeout=5)
        finally:
            p.close()

    asyncio.run(run())
