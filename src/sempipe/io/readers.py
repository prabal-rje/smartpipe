"""Item sources — all with the same shape: ``AsyncIterator[Item]``.

Stdin is read **incrementally** (stage-08 as amended): a daemon pump thread does the
blocking ``readline`` and hands lines to a bounded asyncio queue, so items flow as
they arrive (``tail -f`` works), backpressure is real (the pump stalls when the queue
fills), and shutdown can never hang on a blocked read (the async side is cancellable;
the daemon flag is the last-resort guarantee). ``--in`` file lists stay finite.
"""

from __future__ import annotations

import asyncio
import concurrent.futures
import contextlib
import os
import threading
from dataclasses import dataclass, replace
from pathlib import Path
from typing import TYPE_CHECKING

from sempipe.core.errors import ItemError, SetupFault, UsageFault
from sempipe.io import diagnostics
from sempipe.io.items import Item, item_from_file, item_from_line
from sempipe.models.base import AudioData, ImageData
from sempipe.parsing.detect import FileKind, detect_kind, route
from sempipe.parsing.extract import MissingExtra, extract

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from typing import TextIO

    from sempipe.io.inputs import InputSpec

__all__ = [
    "ensure_not_a_tty",
    "file_items",
    "from_files_items",
    "resolve_items",
    "stdin_items",
]

_HEAD_BYTES = 8192
_QUEUE_MAX = 1024  # lines buffered ahead of consumption — memory stays bounded


def resolve_items(
    spec: InputSpec, stdin: TextIO, *, stop: asyncio.Event | None = None
) -> tuple[AsyncIterator[Item], int | None]:
    """The single entry point every verb uses: dispatch on the input flags.

    Returns ``(items, total)`` — total is known only for ``--in`` file lists;
    stdin is a stream (``tail -f`` works), so its total is ``None`` and the
    spinner shows count+rate instead of an ETA. Only the stdin paths guard
    against a bare terminal."""
    from sempipe.io.inputs import expand_globs

    if spec.patterns and spec.from_files:
        raise UsageFault(
            "--in and --from-files are both file sources — use one\n"
            "  --in takes globs; --from-files reads filenames from stdin."
        )
    if spec.patterns:
        loaded = file_items(expand_globs(spec.patterns))  # UsageFault if no match
        if stdin.isatty():  # files only — no pipe to chain
            return _iter_list(loaded), len(loaded)
        # spec §8: mixed input is files first (glob-sorted), then stdin lines
        return _chain_files_then_stdin(loaded, stdin, stop), None
    ensure_not_a_tty(stdin)
    if spec.from_files:
        return from_files_items(stdin, stop=stop), None
    return stdin_items(stdin, stop=stop), None


async def _chain_files_then_stdin(
    loaded: Sequence[Item], stdin: TextIO, stop: asyncio.Event | None
) -> AsyncIterator[Item]:
    for item in loaded:
        yield item
    async for item in stdin_items(stdin, stop=stop):
        yield item


async def _iter_list(items: Sequence[Item]) -> AsyncIterator[Item]:
    for item in items:
        yield item


@dataclass(frozen=True, slots=True)
class _StdinDocument:
    tmp_name: str
    kind: FileKind


# A queue message: ("line", text) · ("document", _StdinDocument) ·
# ("image", ImageData) · ("fatal", screen) · None = EOF.
_Message = tuple[str, object] | None

_KIND_SUFFIX: dict[FileKind, str] = {
    FileKind.PDF: ".pdf",
    FileKind.DOCX: ".docx",
    FileKind.XLSX: ".xlsx",
    FileKind.PPTX: ".pptx",
    FileKind.HTML: ".html",
    FileKind.EPUB: ".epub",
    FileKind.AUDIO: ".mp3",
}


async def _messages(stdin: TextIO, stop: asyncio.Event | None) -> AsyncIterator[tuple[str, object]]:
    """Incremental stdin source: daemon pump thread → bounded queue → cancellable get.

    Real streams are read with ``os.read`` on the raw fd, NOT ``stdin.readline()``:
    a thread blocked in ``readline`` holds the TextIOWrapper's lock, and CPython's
    interpreter-shutdown finalization then deadlocks trying to close the stream —
    the exact hang the streaming e2e caught. On the fd path the FIRST read also
    sniffs (stage-07 task 4): a binary document redirected to stdin becomes one
    spooled document message; text proceeds as lines with the sniffed bytes as the
    carry. Objects without a usable fd (StringIO in tests) fall back to
    ``readline`` — text-only by construction, never blocking, never sniffed.
    """
    queue: asyncio.Queue[_Message] = asyncio.Queue(_QUEUE_MAX)
    loop = asyncio.get_running_loop()

    def put(message: _Message) -> None:
        asyncio.run_coroutine_threadsafe(queue.put(message), loop).result()

    def pump_text_fd(fd: int, carry: bytes) -> None:
        buffer = bytearray(carry)
        while True:
            while (newline := buffer.find(0x0A)) != -1:
                line = bytes(buffer[: newline + 1])
                del buffer[: newline + 1]
                put(("line", line.decode("utf-8", errors="replace")))
            chunk = os.read(fd, 65536)  # blocks WITHOUT holding any io lock
            if not chunk:
                if buffer:  # final line without a trailing newline
                    put(("line", bytes(buffer).decode("utf-8", errors="replace")))
                return
            buffer += chunk

    def spool_document(fd: int, head: bytes, kind: FileKind) -> None:
        import tempfile

        suffix = _KIND_SUFFIX.get(kind, "")
        with tempfile.NamedTemporaryFile(suffix=suffix, delete=False) as handle:
            handle.write(head)
            while chunk := os.read(fd, 65536):
                handle.write(chunk)
        put(("document", _StdinDocument(handle.name, kind)))

    def collect_image(fd: int, head: bytes) -> None:
        data = bytearray(head)
        while chunk := os.read(fd, 65536):
            data += chunk
        mime = _magic_image_mime(bytes(data[:16]))
        put(("image", ImageData(data=bytes(data), mime=mime)))

    def collect_audio(fd: int, head: bytes, kind: FileKind) -> None:
        data = bytearray(head)
        while chunk := os.read(fd, 65536):
            data += chunk
        from sempipe.parsing.detect import audio_mime

        suffix = _KIND_SUFFIX.get(kind, ".mp3")
        put(("audio", AudioData(data=bytes(data), mime=audio_mime(Path(f"x{suffix}")))))

    def pump_fd(fd: int) -> None:
        head = os.read(fd, _HEAD_BYTES)  # one read — a live stream must not stall here
        if not head:
            return  # empty stdin
        kind = detect_kind(Path("<stdin>"), head)
        match route(kind):
            case "text":
                pump_text_fd(fd, head)
            case "doc":
                spool_document(fd, head, kind)
            case "audio":
                collect_audio(fd, head, kind)
            case "image":
                collect_image(fd, head)
            case "skip":
                from sempipe.cli import screens

                put(("fatal", screens.BINARY_STDIN_UNPARSEABLE))

    def pump_readline() -> None:
        while True:
            line = stdin.readline()  # non-blocking sources only (StringIO et al.)
            if not line:
                return
            put(("line", line))

    def pump() -> None:
        try:
            fd: int | None
            try:
                fd = stdin.fileno()
            except (OSError, ValueError, AttributeError):
                fd = None
            if fd is None:
                pump_readline()
            else:
                pump_fd(fd)
        except (RuntimeError, ValueError, OSError, concurrent.futures.CancelledError):
            # loop closed, consumer gone, or the stream was closed under us —
            # any of these means "stop pumping", never a crash or a stderr trace
            return
        finally:
            sentinel = queue.put(None)  # blocking put — a full queue can't swallow EOF
            try:
                asyncio.run_coroutine_threadsafe(sentinel, loop).result()
            except (RuntimeError, concurrent.futures.CancelledError):
                sentinel.close()  # loop gone — don't leave a never-awaited coroutine

    threading.Thread(target=pump, name="sempipe-stdin-pump", daemon=True).start()
    while True:
        message = await _next_or_stop(queue, stop)
        if message is None:
            return
        yield message


def _magic_image_mime(head: bytes) -> str:
    if head.startswith(b"\x89PNG"):
        return "image/png"
    if head.startswith(b"\xff\xd8\xff"):
        return "image/jpeg"
    if head.startswith((b"GIF87a", b"GIF89a")):
        return "image/gif"
    return "image/webp" if head[8:12] == b"WEBP" else "image/png"


async def _next_or_stop(queue: asyncio.Queue[_Message], stop: asyncio.Event | None) -> _Message:
    if stop is None:
        return await queue.get()
    if stop.is_set():
        return None
    get_task = asyncio.ensure_future(queue.get())
    stop_task = asyncio.ensure_future(stop.wait())
    done, _pending = await asyncio.wait({get_task, stop_task}, return_when=asyncio.FIRST_COMPLETED)
    if get_task in done:
        stop_task.cancel()
        return get_task.result()
    get_task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await get_task  # reap it — no strays, no destroyed-pending warnings
    return None


async def _lines(stdin: TextIO, stop: asyncio.Event | None) -> AsyncIterator[str]:
    """Text-only view of the message stream (``--from-files`` wants filenames)."""
    async for message in _messages(stdin, stop):
        tag, payload = message
        if tag != "line":
            raise SetupFault(
                "error: --from-files expects filenames on stdin, got a binary document\n"
                "  Pipe a list of paths in, e.g.: find . -name '*.md' | sempipe map … --from-files"
            )
        assert isinstance(payload, str)
        yield payload


async def stdin_items(stdin: TextIO, *, stop: asyncio.Event | None = None) -> AsyncIterator[Item]:
    """Each stdin line is one Item, yielded as it arrives (never waits for EOF).

    A redirected binary document (``sempipe map … < report.pdf``) is ONE item:
    spooled, extracted, source ``<stdin>`` (stage-07 task 4). A final line without
    a trailing newline is still an item; empty input yields nothing; CRLF and the
    line-0 BOM are handled per-item by ``item_from_line``.
    """

    index = 0
    async for message in _messages(stdin, stop):
        tag, payload = message
        if tag == "line":
            assert isinstance(payload, str)
            yield item_from_line(payload, index)
            index += 1
        elif tag == "document":
            assert isinstance(payload, _StdinDocument)
            yield await asyncio.to_thread(_extract_stdin_document, payload.tmp_name, payload.kind)
        elif tag == "image":
            assert isinstance(payload, ImageData)
            item = item_from_file("", "<stdin>", 0)
            yield replace(item, media=payload)
        elif tag == "audio":
            assert isinstance(payload, AudioData)
            item = item_from_file("", "<stdin>", 0)
            yield replace(item, media=payload)
        else:  # "fatal"
            assert isinstance(payload, str)
            raise SetupFault(payload)


def _extract_stdin_document(tmp_name: str, kind: FileKind) -> Item:
    from sempipe.cli import screens

    path = Path(tmp_name)
    try:
        extracted = extract(path, kind)
    except MissingExtra as exc:
        raise SetupFault(screens.stdin_document_failed(exc.guidance.splitlines()[0])) from exc
    except ItemError as exc:
        raise SetupFault(screens.stdin_document_failed(str(exc))) from exc
    finally:
        with contextlib.suppress(OSError):
            path.unlink()  # statelessness: the spool never outlives the run
    if extracted.warning is not None:
        diagnostics.warn(f"<stdin>: {extracted.warning}")
    return item_from_file(extracted.text, "<stdin>", 0)


async def from_files_items(
    stdin: TextIO, *, stop: asyncio.Event | None = None
) -> AsyncIterator[Item]:
    """``--from-files``: each non-blank stdin line names a file to read — also
    incremental, so ``find … | sempipe … --from-files`` processes as names arrive."""
    from pathlib import Path

    warned_extras: set[str] = set()
    index = 0
    async for line in _lines(stdin, stop):
        name = line.strip()
        if not name:
            continue
        item = _load_file(Path(name), index, warned_extras)
        if item is not None:
            yield item
            index += 1


def file_items(paths: Sequence[Path]) -> list[Item]:
    """Each file is one item. Unreadable, unparseable, or missing-dependency files
    are skipped with a warning (spec §6.3) — the run never crashes on a bad file."""
    warned_extras: set[str] = set()
    items: list[Item] = []
    for index, path in enumerate(paths):
        item = _load_file(path, index, warned_extras)
        if item is not None:
            items.append(item)
    return items


def _load_file(path: Path, index: int, warned_extras: set[str]) -> Item | None:
    try:
        with path.open("rb") as handle:
            head = handle.read(_HEAD_BYTES)
    except OSError as exc:
        diagnostics.warn(f"skipped: {path} (cannot read: {exc.strerror or exc})")
        return None
    kind = detect_kind(path, head)
    if route(kind) == "audio":
        # D20/post-1.1-02: audio carries its BYTES — transcription became lazy and
        # per-verb (map tries native hearing first; text verbs transcribe on demand)
        from sempipe.parsing.detect import audio_mime

        try:
            data = path.read_bytes()
        except OSError as exc:
            diagnostics.warn(f"skipped: {path} (cannot read: {exc.strerror or exc})")
            return None
        item = item_from_file("", str(path), index)
        return replace(item, media=AudioData(data=data, mime=audio_mime(path)))
    try:
        extracted = extract(path, kind)
    except MissingExtra as exc:
        if exc.extra not in warned_extras:
            diagnostics.warn(exc.guidance)
            warned_extras.add(exc.extra)
        return None
    except ItemError as exc:
        diagnostics.warn(f"skipped: {path} ({exc})")
        return None
    if extracted.warning is not None:
        diagnostics.warn(f"{path}: {extracted.warning}")
    item = item_from_file(extracted.text, str(path), index)
    if extracted.image is not None:
        item = replace(item, media=extracted.image)  # map sends it to a vision model
    return item


def ensure_not_a_tty(stdin: TextIO) -> None:
    """A kind guardrail: bare `sempipe map ...` at a terminal would silently wait."""
    if stdin.isatty():
        raise UsageFault(
            'reading from a terminal — pipe some input in, e.g.: cat notes.txt | sempipe map "..."'
        )
