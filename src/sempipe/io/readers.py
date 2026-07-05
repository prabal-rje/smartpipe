"""Item sources. Batch stdin for now; files (stage 7) and streaming (stage 8)
join later with the same shape: ``AsyncIterator[Item]`` — the runner never
knows where items come from.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from sempipe.core.errors import UsageFault
from sempipe.io.items import Item, item_from_line

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from typing import TextIO

__all__ = ["ensure_not_a_tty", "stdin_items"]


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


def ensure_not_a_tty(stdin: TextIO) -> None:
    """A kind guardrail: bare `sempipe map ...` at a terminal would silently wait."""
    if stdin.isatty():
        raise UsageFault(
            'reading from a terminal — pipe some input in, e.g.: cat notes.txt | sempipe map "..."'
        )
