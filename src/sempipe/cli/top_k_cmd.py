"""``sempipe top_k`` — rank items by similarity to a query."""

from __future__ import annotations

import asyncio
import os
import sys

import click

from sempipe.cli.completions import complete_embed_models
from sempipe.cli.input_options import fields_option, input_options, input_spec
from sempipe.cli.interrupts import graceful_interrupts, settle_budget
from sempipe.core.errors import ExitCode
from sempipe.verbs.top_k import TopKRequest, run_top_k

__all__ = ["top_k_command"]


@click.command(name="top_k")
@click.argument("k", type=int, required=False)
@click.option("--near", required=True, help="Rank items by similarity to this query.")
@click.option("--threshold", type=float, help="Keep everything at or above this similarity (0-1).")
@click.option(
    "--embed-model", "model_flag", shell_complete=complete_embed_models, help="Embedding model."
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel model calls.")
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@click.option("--stream", "stream", is_flag=True, help="Live leaderboard over a stream.")
@fields_option
@click.option(
    "--allow-captions",
    "allow_captions",
    is_flag=True,
    help="Let a CLOUD model convert images/audio to text (paid; local models do it free).",
)
@input_options
def top_k_command(
    k: int | None,
    near: str,
    threshold: float | None,
    model_flag: str | None,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    stream: bool,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Rank items by similarity to a query and return the top K.

    \b
    Examples:
      sempipe top_k 5 --near "distributed systems engineer" --in 'resumes/*.pdf'
      cat corpus.embeddings | sempipe top_k 10 --near "Q3 revenue strategy"
      cat articles.jsonl | sempipe top_k --near "climate policy" --threshold 0.8

    Give a number (K), a --threshold, or both. Each result gains a _score (0-1).
    In file mode, each result is a filename and its score.
    """
    request = TopKRequest(
        allow_captions=allow_captions,
        near=near,
        k=k,
        threshold=threshold,
        model_flag=model_flag,
        concurrency_flag=concurrency_flag,
        stream=stream,
        input=input_spec(in_patterns, from_files=from_files),
        fields=fields,
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: TopKRequest, max_calls: int | None) -> ExitCode:
    from sempipe.container import build_container

    if not request.stream:  # whole-set mode: ^C exits immediately; budget is fatal (D18)
        async with build_container(os.environ, max_calls=max_calls) as container:
            if not request.allow_captions and container.config.allow_captions:
                from dataclasses import replace as _replace

                request = _replace(request, allow_captions=True)  # profile consent (D35)
            return await run_top_k(request, container, stdin=sys.stdin, stdout=sys.stdout)
    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            from dataclasses import replace as _replace

            request = _replace(request, allow_captions=True)  # profile consent (D35)
        code = await run_top_k(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
