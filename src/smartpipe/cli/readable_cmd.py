"""``smartpipe readable`` — render records for human eyes."""

from __future__ import annotations

import asyncio
import sys

import click

from smartpipe.core.errors import ExitCode
from smartpipe.io import tty
from smartpipe.verbs.readable import ReadableRequest, run_readable

__all__ = ["readable_command"]


@click.command(name="readable")
@click.option("--full", "full", is_flag=True, help="Show whole values (no truncation).")
@click.option("--bare", "bare", is_flag=True, help="Drop the __ metadata fields entirely.")
def readable_command(full: bool, bare: bool) -> None:
    """Render each item as a readable block — for eyes, not parsers.

    \b
    Examples:
      cat results.jsonl | smartpipe readable | less -R
      … | smartpipe map "Extract {vendor, total}" | smartpipe readable > report.txt

    The same block layout as the terminal preview: nested maps indent, lists
    as '- ', long values truncated (--full shows everything), the __ metadata
    dimmed at the bottom (--bare drops it). Color only when stdout is a
    terminal. Plain text items pass through unchanged.
    """
    request = ReadableRequest(full=full, bare=bare, color=tty.stdout_supports_color())
    code = asyncio.run(run_readable(request, stdin=sys.stdin, stdout=sys.stdout))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))
