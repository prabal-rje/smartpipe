"""Hidden debug verb: pass input through the io spine untouched.

Not listed in help on purpose — it exists so anyone (including future us,
debugging a support question) can see exactly how smartpipe itemizes and
serializes a given input: ``cat mystery.jsonl | smartpipe echo --output json``.
It is also the standing integration test of the whole io layer.
"""

from __future__ import annotations

import asyncio
import os
import sys

import click

from smartpipe.io import readers, tty
from smartpipe.io.writers import OutputFormat, WriterConfig, make_writer, resolve_format

__all__ = ["echo_command"]


@click.command(name="echo", hidden=True)
@click.option(
    "--output",
    "output_format",
    type=click.Choice([fmt.value for fmt in OutputFormat]),
    default=OutputFormat.AUTO.value,
    show_default=True,
    help="Output format.",
)
def echo_command(output_format: str) -> None:
    """Pass stdin through smartpipe's item pipeline unchanged (debugging aid)."""
    asyncio.run(_run(OutputFormat(output_format)))


async def _run(flag: OutputFormat) -> None:
    readers.ensure_not_a_tty(sys.stdin)
    mode = resolve_format(flag, os.environ, stdout_tty=tty.stdout_is_tty(), structured=False)
    config = WriterConfig(mode=mode, color=tty.stdout_supports_color(), width=tty.terminal_width())
    writer = make_writer(config, sys.stdout)
    async for item in readers.stdin_items(sys.stdin):
        writer.write_passthrough(item)
    writer.flush()
