"""``smartpipe graph`` — the entity co-occurrence graph (free with --fast)."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from smartpipe.cli.input_options import input_options, input_spec, positional_paths
from smartpipe.cli.interrupts import graceful_interrupts
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.graph import GraphRequest, run_graph

__all__ = ["graph_command"]


@click.command(name="graph")
@click.argument("paths", nargs=-1, required=False)
@click.option(
    "--fast",
    is_flag=True,
    help="The free mode: local NER + co-occurrence — zero model calls, on-device.",
)
@click.option(
    "--entities",
    "entities",
    metavar='"a, b"',
    help='Entity types to find (default: "person, organization, location").',
)
@click.option(
    "--window",
    type=click.Choice(["sentence", "chunk", "document"]),
    default="chunk",
    show_default=True,
    help="How close 'together' is: one sentence, one item, or one document.",
)
@click.option(
    "--min-weight",
    "min_weight",
    type=int,
    default=1,
    show_default=True,
    help="Drop edges co-occurring fewer than N times.",
)
@click.option(
    "--save",
    "save_path",
    metavar="PATH",
    help="Also write .graphml/.dot/.mmd/.csv/.html — or a directory/ for an Obsidian vault.",
)
@click.option(
    "--top",
    type=int,
    help="Cap display formats (dot/mmd/html) to the N biggest hubs.",
)
@input_options
def graph_command(
    fast: bool,
    entities: str | None,
    window: str,
    min_weight: int,
    save_path: str | None,
    top: int | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
    strict_rows: bool,
    paths: tuple[str, ...],
) -> None:
    """Build an entity co-occurrence graph — free and on-device with --fast.

    \b
    Examples:
      smartpipe graph --fast notes/*.md --save graph.html
      cat corpus.jsonl | smartpipe graph --fast --entities "person, vessel, account"
      smartpipe sample 200 < corpus.jsonl | smartpipe graph --fast    (preview on a slow machine)

    stdout is JSONL edges — {"source", "relation", "target", "weight",
    "sources"} — sorted heaviest first, with spine-ref provenance on every
    edge. --fast finds the entities you name with a local NER model (one
    ~190 MB download, then free forever), folds near-duplicate names, and
    counts co-occurrence inside --window. Zero model calls; nothing leaves
    the machine.
    """
    request = GraphRequest(
        fast=fast,
        entities=entities,
        window=window,
        min_weight=min_weight,
        save=save_path,
        top=top,
        input=input_spec(
            positional_paths(paths, in_patterns),
            from_files=from_files,
            as_mode=as_mode,
            strict_rows=strict_rows,
        ),
    )
    code = asyncio.run(_run(request))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: GraphRequest) -> ExitCode:
    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, stop=stop) as container,
    ):
        return await run_graph(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
