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
    terminal. Plain text items pass through unchanged. At a color terminal,
    media items render a thumbnail/waveform/frame-strip preview under their
    summary line (persisted kill switch: smartpipe config media-previews off).
    """
    import os

    from smartpipe.config.paths import config_path
    from smartpipe.config.store import load_config
    from smartpipe.io.preview import maybe_preview

    color = tty.stdout_supports_color()
    config = load_config(config_path(os.environ))
    media_lines = maybe_preview(
        enabled=config.media_previews is not False,
        color=color,
        width=tty.terminal_width(),
    )
    request = ReadableRequest(full=full, bare=bare, color=color)
    code = asyncio.run(
        run_readable(request, stdin=sys.stdin, stdout=sys.stdout, media_lines=media_lines)
    )
    if code is not ExitCode.OK:
        raise SystemExit(int(code))
