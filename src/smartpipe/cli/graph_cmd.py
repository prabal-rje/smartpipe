"""``smartpipe graph`` — the entity/relationship graph (free with --fast, model-read
with a focus prompt or --name-top, adoption for edge records on stdin)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from smartpipe.cli.completions import complete_chat_models
from smartpipe.cli.input_options import (
    input_options,
    input_spec,
    ocr_model_option,
    positional_paths,
)
from smartpipe.cli.interrupts import graceful_interrupts
from smartpipe.cli.manifest_option import begin_manifest, manifest_option, settled
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.graph import GraphRequest, run_graph

__all__ = ["graph_command"]


@click.command(name="graph")
@click.argument("args", nargs=-1, required=False)
@click.option(
    "--fast",
    is_flag=True,
    help="The free mode: local NER + co-occurrence — zero model calls, on-device.",
)
@click.option(
    "--entities",
    "entities",
    metavar='"a, b"',
    help='Entity types to find (default: "person, organization, location"); '
    "with a focus prompt they become the subject/object type enum.",
)
@click.option(
    "--relations",
    "relations",
    metavar='"pays, owns"',
    help="Closed relation vocabulary for the model-read modes (typed ontology).",
)
@click.option(
    "--name-top",
    "name_top",
    type=int,
    metavar="N",
    help="Hybrid mode: free co-occurrence pass, then one call per edge names "
    "the N strongest relations.",
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
@click.option(
    "--model",
    "model_flag",
    shell_complete=complete_chat_models,
    help="Model for the extraction/naming calls (e.g. ollama/qwen3:8b).",
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option(
    "--max-calls",
    "max_calls",
    type=int,
    help="Stop after N billable units (model calls; dedicated OCR pages).",
)
@manifest_option
@ocr_model_option
@input_options
def graph_command(
    fast: bool,
    manifest_path: Path | None,
    entities: str | None,
    relations: str | None,
    name_top: int | None,
    window: str,
    min_weight: int,
    save_path: str | None,
    top: int | None,
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    ocr_model_flag: str | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
    strict_rows: bool,
    args: tuple[str, ...],
) -> None:
    """Build an entity/relationship graph — free with --fast, model-read with a focus.

    \b
    Examples:
      smartpipe graph --fast notes/*.md --save graph.html
      cat corpus.jsonl | smartpipe graph --fast --entities "person, vessel, account"
      smartpipe sample 200 < corpus.jsonl | smartpipe graph --fast    (preview on a slow machine)
      smartpipe graph "who pays whom" filings/*.pdf --max-calls 500
      smartpipe graph "deal flow" --name-top 200 notes/*.md
      cat edges.jsonl | smartpipe graph --save deals.graphml    (adopt your own edge records)

    stdout is JSONL edges — {"source", "relation", "target", "weight",
    "sources"} — sorted heaviest first, with spine-ref provenance on every
    edge. --fast finds the entities you name with a local NER model (one
    ~190 MB download, then free forever), folds near-duplicate names, and
    counts co-occurrence inside --window. Zero model calls; corpus data stays
    on the machine.

    \b
    The model-read modes (spend, belted by --max-calls):
      a focus prompt      chunk + extract typed triples per chunk; the cost plan
                          prints before any spend, and a belt smaller than the
                          need builds a disclosed partial graph (exit 1).
      --name-top N        free pass first, then one call per edge names the N
                          strongest relations; a belt shortfall keeps co-occurs.
      --entities/--relations compile to enum constraints (typed ontology).

    Without --fast the first positional argument is the focus prompt; edge
    records piped to stdin ({"source","target"} or {"subject","relation",
    "object"}) skip extraction entirely and just fold + serialize.
    """
    if fast:
        focus, paths = None, args
    elif args:
        focus, paths = args[0], args[1:]
    else:
        focus, paths = None, ()
    request = GraphRequest(
        fast=fast,
        focus=focus,
        entities=entities,
        relations=relations,
        name_top=name_top,
        window=window,
        min_weight=min_weight,
        save=save_path,
        top=top,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        ocr_model_flag=ocr_model_flag,
        input=input_spec(
            positional_paths(paths, in_patterns),
            from_files=from_files,
            as_mode=as_mode,
            strict_rows=strict_rows,
        ),
    )
    code = asyncio.run(_run(request, max_calls, manifest_path))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(
    request: GraphRequest, max_calls: int | None, manifest_path: Path | None
) -> ExitCode:
    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        begin_manifest(manifest_path, verb="graph", prompt=request.focus)
        return await settled(
            run_graph(
                request,
                container,
                stdin=sys.stdin,
                stdout=sys.stdout,
                stop=stop,
                budget=container.budget,
            ),
            None,  # graph settles its own belt inside run_graph
        )
