"""``smartpipe outliers`` — the items least like the rest."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from smartpipe.cli.completions import complete_embed_models
from smartpipe.cli.input_options import (
    input_options,
    input_spec,
    ocr_model_option,
    positional_paths,
)
from smartpipe.cli.interrupts import graceful_interrupts, settle_budget
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
@click.option(
    "--allow-captions",
    "allow_captions",
    is_flag=True,
    help="Let a CLOUD model convert images/audio to text (paid; local models do it free).",
)
@ocr_model_option
@input_options
def outliers_command(
    count: int,
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    ocr_model_flag: str | None,
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
        ocr_model_flag=ocr_model_flag,
        input=input_spec(
            positional_paths(paths, in_patterns),
            from_files=from_files,
            as_mode=as_mode,
            strict_rows=strict_rows,
        ),
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: OutliersRequest, max_calls: int | None) -> ExitCode:
    from dataclasses import replace

    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            request = replace(request, allow_captions=True)  # profile consent (D35)
        code = await run_outliers(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
