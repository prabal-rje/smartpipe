"""``smartpipe write`` — the egress door: route items to files."""

from __future__ import annotations

import asyncio
import sys

import click

from smartpipe.core.errors import ExitCode
from smartpipe.verbs.write import WriteRequest, run_write

__all__ = ["write_command"]


@click.command(name="write")
@click.argument("template")
@click.option(
    "--field",
    "field",
    metavar="FIELD",
    help="Write ONE field's value as raw text instead of JSONL rows.",
)
@click.option(
    "--keep-meta",
    "keep_meta",
    is_flag=True,
    help="Keep the __ metadata fields in written rows (stripped by default).",
)
@click.option(
    "--as",
    "as_mode",
    type=click.Choice(["file", "lines"]),
    default=None,
    help="Override the mirror: file = one file per item; lines = append rows.",
)
def write_command(template: str, field: str | None, keep_meta: bool, as_mode: str | None) -> None:
    """Route items to files — the write half of the read/write mirror.

    \b
    Examples:
      cat results.jsonl | smartpipe write 'out/{stem}.jsonl'
      … | smartpipe map "translate" | smartpipe write 'fr/{name}'
      … | smartpipe write 'by-lang/{lang}.jsonl'          # content fan-out
      … | smartpipe write 'figs/{stem}-{index}.png'       # media: one file each

    Template vars: {name} {stem} {ext} {path} {index}, plus any record field.
    File-cut items (and media) each get their own file; line/row-cut items
    append into their target, reassembled in spine order. The paths written
    land on stdout, one per line, so the pipe continues.
    """
    code = asyncio.run(
        _run(WriteRequest(template=template, keep_meta=keep_meta, field=field, as_mode=as_mode))
    )
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: WriteRequest) -> ExitCode:
    return await run_write(request, stdin=sys.stdin, stdout=sys.stdout)
