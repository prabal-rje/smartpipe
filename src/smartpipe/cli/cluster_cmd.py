"""``smartpipe cluster`` — themes with sizes and quotes."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from smartpipe.cli.completions import complete_chat_models, complete_embed_models
from smartpipe.cli.input_options import input_options, input_spec, positional_paths
from smartpipe.cli.interrupts import graceful_interrupts, settle_budget
from smartpipe.core.errors import ExitCode
from smartpipe.verbs.cluster import ClusterRequest, run_cluster

__all__ = ["cluster_command"]


@click.command(name="cluster")
@click.argument("paths", nargs=-1, required=False)
@click.option("--k", type=int, help="Force exactly K clusters (merge smallest).")
@click.option("--top", type=int, help="Show N clusters; fold the rest into (other).")
@click.option(
    "--explode",
    metavar="members",
    help="One row per input item, labeled with its cluster.",
)
@click.option(
    "--model",
    "model_flag",
    shell_complete=complete_chat_models,
    help="Chat model for cluster labels.",
)
@click.option(
    "--embed-model",
    "embed_flag",
    shell_complete=complete_embed_models,
    help="Embedding model for grouping.",
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
def cluster_command(
    k: int | None,
    top: int | None,
    explode: str | None,
    model_flag: str | None,
    embed_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
    paths: tuple[str, ...],
) -> None:
    """Group items by meaning and label each group — themes, sized, with quotes.

    \b
    Examples:
      cat feedback.txt | smartpipe cluster
      cat tickets.jsonl | smartpipe cluster --top 8 | smartpipe chart cluster
      cat snippets.txt | smartpipe cluster --explode members > coded.jsonl

    One row per cluster, largest first: {"cluster", "size", "share",
    "examples"} — the examples are the most representative quotes. The cost
    shape is the point: N embeddings + one label call per cluster, never N
    chat calls. Labels are deterministic (temperature 0): re-runs don't
    change your slide.
    """
    request = ClusterRequest(
        k=k,
        top=top,
        explode=explode,
        model_flag=model_flag,
        embed_flag=embed_flag,
        concurrency_flag=concurrency_flag,
        allow_captions=allow_captions,
        input=input_spec(
            positional_paths(paths, in_patterns), from_files=from_files, as_mode=as_mode
        ),
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: ClusterRequest, max_calls: int | None) -> ExitCode:
    from dataclasses import replace

    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            request = replace(request, allow_captions=True)  # profile consent (D35)
        code = await run_cluster(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
