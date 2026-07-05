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
import threading
from typing import TYPE_CHECKING

from sempipe.core.errors import ItemError, UsageFault
from sempipe.io import diagnostics
from sempipe.io.items import Item, item_from_file, item_from_line
from sempipe.parsing.detect import detect_kind
from sempipe.parsing.extract import MissingExtra, extract

if TYPE_CHECKING:
    from collections.abc import AsyncIterator, Sequence
    from pathlib import Path
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


async def resolve_items(spec: InputSpec, stdin: TextIO) -> AsyncIterator[Item]:
    """The single entry point every verb uses: dispatch on the input flags.
    ``--in`` reads files; ``--from-files`` reads filenames from stdin; otherwise
    stdin lines. Only the stdin paths guard against a bare terminal."""
    from sempipe.io.inputs import expand_globs

    if spec.patterns:
        for item in file_items(expand_globs(spec.patterns)):  # UsageFault if no match
            yield item
    elif spec.from_files:
        ensure_not_a_tty(stdin)
        async for item in from_files_items(stdin):
            yield item
    else:
        ensure_not_a_tty(stdin)
        async for item in stdin_items(stdin):
            yield item


async def _lines(stdin: TextIO, stop: asyncio.Event | None) -> AsyncIterator[str]:
    """Incremental line source: daemon pump thread → bounded queue → cancellable get.

    The pump's blocking ``queue.put(...).result()`` is the backpressure (the thread
    stalls while the queue is full) and the shutdown path (when the async side goes
    away, the pending put is cancelled and the thread exits). ``None`` is the EOF
    sentinel, delivered with a *blocking* put so a full queue can't swallow it.
    """
    queue: asyncio.Queue[str | None] = asyncio.Queue(_QUEUE_MAX)
    loop = asyncio.get_running_loop()

    def pump() -> None:
        try:
            while True:
                line = stdin.readline()  # blocks in this thread only
                if not line:
                    break  # EOF
                asyncio.run_coroutine_threadsafe(queue.put(line), loop).result()
        except (RuntimeError, ValueError, OSError, concurrent.futures.CancelledError):
            # loop closed, consumer gone, or the stream was closed under us —
            # any of these means "stop pumping", never a crash or a stderr trace
            return
        finally:
            with contextlib.suppress(RuntimeError, concurrent.futures.CancelledError):
                asyncio.run_coroutine_threadsafe(queue.put(None), loop).result()

    threading.Thread(target=pump, name="sempipe-stdin-pump", daemon=True).start()
    while True:
        line = await _next_or_stop(queue, stop)
        if line is None:
            return
        yield line


async def _next_or_stop(queue: asyncio.Queue[str | None], stop: asyncio.Event | None) -> str | None:
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


async def stdin_items(stdin: TextIO, *, stop: asyncio.Event | None = None) -> AsyncIterator[Item]:
    """Each stdin line is one Item, yielded as it arrives (never waits for EOF).

    A final line without a trailing newline is still an item; empty input yields
    nothing; CRLF and the line-0 BOM are handled per-item by ``item_from_line``.
    """
    index = 0
    async for line in _lines(stdin, stop):
        yield item_from_line(line, index)
        index += 1


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
    if extracted.image is not None:
        diagnostics.warn(
            f"skipped: {path} (image input needs a vision model — arriving in a later release)"
        )
        return None
    if extracted.warning is not None:
        diagnostics.warn(f"{path}: {extracted.warning}")
    return item_from_file(extracted.text, str(path), index)


def ensure_not_a_tty(stdin: TextIO) -> None:
    """A kind guardrail: bare `sempipe map ...` at a terminal would silently wait."""
    if stdin.isatty():
        raise UsageFault(
            'reading from a terminal — pipe some input in, e.g.: cat notes.txt | sempipe map "..."'
        )
