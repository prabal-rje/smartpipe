"""``smartpipe join`` — the CLI surface: flags in, verb out (D21)."""

from __future__ import annotations

import asyncio
import os
import sys
from pathlib import Path

import click

from smartpipe.cli.completions import complete_chat_models, complete_embed_models
from smartpipe.cli.input_options import (
    fields_option,
    input_options,
    input_spec,
    positional_paths,
    resolve_prompt,
)
from smartpipe.cli.interrupts import graceful_interrupts, settle_budget
from smartpipe.core.errors import ExitCode
from smartpipe.io.writers import OutputFormat
from smartpipe.verbs.join import JoinRequest, run_join

__all__ = ["join_command"]


@click.command(name="join")
@click.argument("predicate", required=False)
@click.option(
    "--on",
    "on_keys",
    multiple=True,
    metavar="EXPR",
    help="Key equality: 'left.FIELD == right.FIELD' (repeatable, AND-ed). "
    "Alone: a free deterministic join; with a prompt: blocks the candidate pairs.",
)
@click.argument("paths", nargs=-1, required=False)
@click.option(
    "--prompt-file",
    "prompt_file",
    type=click.Path(path_type=Path),
    help="Read the predicate from a file (the @file shorthand does the same).",
)
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
    "--kind",
    type=click.Choice(["inner", "leftouter", "anti"]),
    default="inner",
    show_default=True,
    help="inner: matched pairs · leftouter: all left rows (null right) · "
    "anti: only UNMATCHED left rows, verbatim.",
)
@click.option(
    "--unmatched",
    "unmatched",
    type=click.Path(path_type=Path),
    help="Write left items with zero matches to FILE, verbatim (inner only).",
)
@click.option(
    "--full",
    "full",
    is_flag=True,
    help="Terminal preview: show whole values (no truncation).",
)
@click.option(
    "--bare",
    "bare",
    is_flag=True,
    help="Strip __ metadata fields from record output (for > redirections).",
)
@click.option(
    "--fallback-model",
    "fallback_flag",
    shell_complete=complete_chat_models,
    help="Chat model to switch to if the primary looks down (circuit breaker).",
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
@click.option(
    "--allow-captions",
    "allow_captions",
    is_flag=True,
    help="Let a CLOUD model convert images/audio to text (paid; local models do it free).",
)
@input_options
def join_command(
    predicate: str | None,
    on_keys: tuple[str, ...],
    prompt_file: Path | None,
    right: Path,
    k: int,
    threshold: float | None,
    unmatched: Path | None,
    kind: str,
    model_flag: str | None,
    fallback_flag: str | None,
    bare: bool,
    full: bool,
    embed_model_flag: str | None,
    output: str,
    concurrency_flag: int | None,
    max_calls: int | None,
    allow_captions: bool,
    fields: tuple[str, ...] | None,
    in_patterns: tuple[str, ...],
    from_files: bool,
    as_mode: str | None,
    strict_rows: bool,
    paths: tuple[str, ...],
) -> None:
    """Match stdin against a second input, semantically. Emits matched pairs.

    \b
    Examples:
      cat tickets.jsonl | smartpipe join "{left.text} concerns {right.name}" --right products.jsonl
      tail -f events.log | smartpipe join "{left.text} involves {right.name}" --right people.jsonl
      cat orders.jsonl | smartpipe join "the same purchase" --right invoices.jsonl --kind anti

    Each brace names a side's field ({left.x} / {right.x}; .text is the whole
    item). The right side is embedded once and indexed; each left item is
    compared to its --k nearest candidates and only those pairs are judged by
    the chat model — so cost is lines x k, never lines x right-size.
    Output: {"left": {...}, "right": {...}, "__score": ...} per matched pair.
    """
    request = JoinRequest(
        allow_captions=allow_captions,
        predicate=(
            None
            if on_keys and predicate is None and prompt_file is None
            else resolve_prompt(predicate, prompt_file)
        ),
        right=right,
        k=k,
        threshold=threshold,
        unmatched=unmatched,
        kind=kind,
        model_flag=model_flag,
        fallback_flag=fallback_flag,
        on=on_keys,
        bare=bare,
        full=full,
        embed_model_flag=embed_model_flag,
        concurrency_flag=concurrency_flag,
        output=OutputFormat(output),
        input=input_spec(
            positional_paths(paths, in_patterns), from_files=from_files, as_mode=as_mode
        ),
        fields=fields,
    )
    code = asyncio.run(_run(request, max_calls))
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _run(request: JoinRequest, max_calls: int | None) -> ExitCode:
    from smartpipe.container import build_container

    async with (
        graceful_interrupts() as stop,
        build_container(os.environ, max_calls=max_calls, stop=stop) as container,
    ):
        if not request.allow_captions and container.config.allow_captions:
            from dataclasses import replace as _replace

            request = _replace(request, allow_captions=True)  # profile consent (D35)
        code = await run_join(request, container, stdin=sys.stdin, stdout=sys.stdout, stop=stop)
        return settle_budget(container.budget, code)
