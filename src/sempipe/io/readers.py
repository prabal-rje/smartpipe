"""Item sources. Batch stdin for now; files (stage 7) join with the same shape:
``AsyncIterator[Item]`` — the runner never knows where items come from.
"""

from __future__ import annotations

import asyncio
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


async def stdin_items(stdin: TextIO) -> AsyncIterator[Item]:
    """Batch mode: drain the pipe, yield one Item per line, in order.

    A final line without a trailing newline is still an item; empty input
    yields nothing. Splitting is on ``\\n`` only (grep semantics) — CRLF is
    handled per-item by ``item_from_line``.
    """
    text = await asyncio.to_thread(stdin.read)
    if not text:
        return
    lines = text.split("\n")
    if lines[-1] == "":
        lines.pop()
    for index, line in enumerate(lines):
        yield item_from_line(line, index)


async def from_files_items(stdin: TextIO) -> AsyncIterator[Item]:
    """``--from-files``: each non-blank stdin line names a file to read."""
    from pathlib import Path

    text = await asyncio.to_thread(stdin.read)
    paths = [Path(line.strip()) for line in text.splitlines() if line.strip()]
    for item in file_items(paths):
        yield item


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
