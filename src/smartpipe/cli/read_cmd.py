"""Reader mode (item 16): the binary IS the reader.

``smartpipe PATH…`` — a first argument that is no verb but exists on disk —
emits the files' items to stdout as JSONL records, cut per the ``--as`` dial,
each carrying its ``__`` spine (``__source``, ``__media``). Zero model calls:
this is ingestion made visible, the front half of the read/write mirror.
"""

from __future__ import annotations

import asyncio
import sys

import click

from smartpipe.core.errors import ExitCode
from smartpipe.io.inputs import InputSpec
from smartpipe.io.items import item_record

__all__ = ["read_command"]


@click.command(name="read", hidden=True)
@click.argument("paths", nargs=-1, required=True)
@click.option(
    "--bare",
    "bare",
    is_flag=True,
    help="Strip __ metadata fields from the emitted records.",
)
@click.option(
    "--as",
    "as_mode",
    type=click.Choice(["file", "lines", "jsonl"]),
    default=None,
    help="Cut granularity: file = one item per file; lines = text rows; jsonl = strict records.",
)
def read_command(paths: tuple[str, ...], as_mode: str | None, bare: bool) -> None:
    """Emit the named files' items as JSONL records (reader mode).

    \b
    Examples:
      smartpipe report.pdf                     # one record: the whole document
      smartpipe notes.txt --as lines           # one record per line
      smartpipe 'logs/*.jsonl'                 # strict records, per row
    """
    code = asyncio.run(_run(InputSpec(patterns=paths, from_files=False, as_mode=as_mode), bare))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(spec: InputSpec, bare: bool) -> ExitCode:
    from smartpipe.io.readers import resolve_items
    from smartpipe.io.writers import RenderMode, WriterConfig, make_writer

    items, _total = resolve_items(spec, sys.stdin)
    # records for machines, always — reader mode's whole output IS the record
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, bare=bare), sys.stdout
    )
    produced = 0
    async for item in items:
        writer.write_record(item_record(item))
        produced += 1
    writer.flush()
    return ExitCode.OK if produced else ExitCode.PARTIAL
