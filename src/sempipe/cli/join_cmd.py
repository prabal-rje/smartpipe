"""``sempipe join`` — the CLI surface: flags in, verb out (D21)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from sempipe.cli.completions import complete_chat_models, complete_embed_models
from sempipe.cli.input_options import fields_option, input_options, input_spec
from sempipe.cli.interrupts import graceful_interrupts, settle_budget
from sempipe.core.errors import ExitCode
from sempipe.io.writers import OutputFormat
from sempipe.verbs.join import JoinRequest, run_join

__all__ = ["join_command"]


@click.command(name="join")
@click.argument("predicate")
@click.option(
    "--right",
    "right",
    required=True,
    type=click.Path(dir_okay=False, path_type=Path, allow_dash=True),
    help="The finite side to index (JSONL or plain lines). stdin is the left side.",
)
@click.option(
    "--k",
    "k",
    type=int,
    default=5,
    show_default=True,
    help="Candidates judged per left item (the recall knob — see the docs).",
)
@click.option(
    "--threshold",
    type=float,
    help="Similarity floor (0-1) a candidate must clear before judging.",
)
@click.option(
    "--model",
    "model_flag",
    shell_complete=complete_chat_models,
    help="Chat model for the judge calls.",
)
@click.option(
    "--embed-model",
    "embed_model_flag",
    shell_complete=complete_embed_models,
    help="Embedding model for both sides.",
)
@click.option(
    "--output",
    type=click.Choice([fmt.value for fmt in OutputFormat]),
    default=OutputFormat.AUTO.value,
    show_default=True,
    help="Output format.",
)
@click.option("--concurrency", "concurrency_flag", type=int, help="Max parallel left items.")
@click.option("--max-calls", "max_calls", type=int, help="Stop after N model calls (cost cap).")
@fields_option
@input_options
def join_command(
    predicate: str,
    right: Path,
    k: int,
    threshold: float | None,
    model_flag: str | None,
    embed_model_flag: str | None,
    output: str,
    concurrency_flag: int | None,
    max_calls: int | None,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
) -> None:
    """Match stdin against a second input, semantically. Emits matched pairs.

    \b
    Examples:
      cat tickets.jsonl | sempipe join "{left.text} concerns {right.name}" --right products.jsonl
      tail -f events.log | sempipe join "{left.text} involves {right.name}" --right people.jsonl

    Each brace names a side's field ({left.x} / {right.x}; .text is the whole
    item). The right side is embedded once and indexed; each left item is
    compared to its --k nearest candidates and only those pairs are judged by
    the chat model — so cost is lines x k, never lines x right-size.
    Output: {"left": {...}, "right": {...}, "_score": ...} per matched pair.
    """
    request = JoinRequest(
        predicate=predicate,
        right=right,
        k=k,
        threshold=threshold,
        model_flag=model_flag,
        embed_model_flag=embed_model_flag,
        concurrency_flag=concurrency_flag,
        output=OutputFormat(output),
        input=input_spec(in_patterns, from_files=from_files),
        fields=fields,
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: JoinRequest, max_calls: int | None) -> ExitCode:
    from sempipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        code = await run_join(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
