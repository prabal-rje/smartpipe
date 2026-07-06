"""``sempipe distinct`` — fold near-duplicates; keep the first of each."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from sempipe.cli.completions import complete_embed_models
from sempipe.cli.input_options import input_options, input_spec
from sempipe.cli.interrupts import graceful_interrupts, settle_budget
from sempipe.core.errors import ExitCode
from sempipe.verbs.distinct import DistinctRequest, run_distinct

__all__ = ["distinct_command"]


@click.command(name="distinct")
@click.option("--show-groups", is_flag=True, help="Emit group records instead (audit the folds).")
@click.option(
    "--threshold",
    type=float,
    default=0.90,
    show_default=True,
    help="Cosine similarity at which two items are the same thing.",
)
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
@input_options
def distinct_command(
    show_groups: bool,
    threshold: float,
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Fold near-duplicate items — the same thing worded differently is one item.

    \b
    Examples:
      cat tickets.txt | sempipe distinct > unique.txt
      cat alerts.jsonl | sempipe distinct --show-groups   # audit what folded
      sempipe distinct < candidates.jsonl > train-clean.jsonl

    Exact duplicates fold for free (no model calls); the rest are embedded
    once and grouped by meaning. First occurrence wins; output keeps input
    order and bytes. The receipt on stderr says exactly what was folded.

    Run distinct BEFORE map/filter: every duplicate you fold is a model call
    you don't pay for downstream.
    """
    request = DistinctRequest(
        show_groups=show_groups,
        threshold=threshold,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        allow_captions=allow_captions,
        input=input_spec(in_patterns, from_files=from_files),
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: DistinctRequest, max_calls: int | None) -> ExitCode:
    from dataclasses import replace

    from sempipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            request = replace(request, allow_captions=True)  # profile consent (D35)
        code = await run_distinct(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
