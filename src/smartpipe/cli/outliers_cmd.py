"""``smartpipe outliers`` — the items least like the rest."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from smartpipe.cli.completions import complete_embed_models
from smartpipe.cli.input_options import input_options, input_spec, positional_paths
from smartpipe.cli.interrupts import graceful_interrupts
from smartpipe.cli.manifest_option import begin_manifest, manifest_option, settled
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.outliers import OutliersRequest, run_outliers

__all__ = ["outliers_command"]


@click.command(name="outliers")
@click.argument("count", type=int, default=5)
@click.argument("paths", nargs=-1, required=False)
@click.option(
    "--embed-model",
    "model_flag",
    shell_complete=complete_embed_models,
    help="Embedding model for this run.",
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@manifest_option
@click.option(
    "--allow-captions",
    "allow_captions",
    is_flag=True,
    help="Let a CLOUD model convert images/audio to text (paid; local models do it free).",
)
@input_options
def outliers_command(
    count: int,
    manifest_path: Path | None,
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
    strict_rows: bool,
    paths: tuple[str, ...],
) -> None:
    """Rank the N items least like the rest — novelty, surfaced.

    \b
    Examples:
      cat today.log | smartpipe outliers 5        # the failure shapes you HAVEN'T seen
      cat train.jsonl | smartpipe outliers 20     # label noise and template glitches

    top_k's mirror: farthest from everything instead of nearest to a query.
    Embeddings only — no chat calls. Each row carries __distance, and the
    stderr line anchors it ("median neighbor distance 0.21 — these are
    3.1x-3.9x out") so the score means something.
    """
    request = OutliersRequest(
        count=count,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        allow_captions=allow_captions,
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
    request: OutliersRequest, max_calls: int | None, manifest_path: Path | None
) -> ExitCode:
    from dataclasses import replace

    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            request = replace(request, allow_captions=True)  # profile consent (D35)
        begin_manifest(manifest_path, verb="outliers")
        return await settled(
            run_outliers(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop),
            container.budget,
        )
