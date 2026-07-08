"""The ``readable`` verb (wave 2, item 25): the human door, explicitly.

``… | smartpipe readable`` renders each incoming item in the same YAML-ish
block format as the TTY preview (item 19 — one renderer, two homes), for
EYES: pipe it to ``less``, a file, or a report. ANSI color only when ITS
stdout is a terminal; plain layout otherwise. Text items pass through
unchanged; records render as blocks separated by a blank line. Zero model
calls.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode
from smartpipe.io import readers
from smartpipe.io.inputs import STDIN
from smartpipe.io.render import render_block

if TYPE_CHECKING:
    import asyncio
    from typing import TextIO

    from smartpipe.io.render import MediaLines

__all__ = ["ReadableRequest", "run_readable"]


@dataclass(frozen=True, slots=True)
class ReadableRequest:
    full: bool = False  # --full: no truncation
    bare: bool = False  # --bare: drop the __ spine entirely
    color: bool = False  # ANSI only when readable's own stdout is a terminal


async def run_readable(
    request: ReadableRequest,
    *,
    stdin: TextIO,
    stdout: TextIO,
    stop: asyncio.Event | None = None,
    media_lines: MediaLines | None = None,
) -> ExitCode:
    """``media_lines`` is the injected TTY media-preview hook (io/preview) —
    None (pipes, NO_COLOR, the media-previews kill switch) keeps every byte
    identical to the plain block rendering. ``--bare`` strips ``__media``
    before rendering, so the hook never fires there."""
    items_iter, _total = readers.resolve_items(STDIN, stdin, stop=stop)
    async for item in items_iter:
        if stop is not None and stop.is_set():
            break
        if item.data is None:
            stdout.write(f"{item.raw}\n\n")  # text passes through, block-spaced
            continue
        record = item.data
        if request.bare:
            record = {key: value for key, value in record.items() if not key.startswith("__")}
        block = render_block(
            record, color=request.color, full=request.full, media_lines=media_lines
        )
        stdout.write(block + "\n\n")
    stdout.flush()
    return ExitCode.OK
