"""The ``config`` verb: show settings, set a model, or run interactive setup.

The interactive flow (``run_interactive_setup``) takes its I/O as injected
callables (ask/confirm/say/save) so it is unit-testable without a real terminal
— the click wiring supplies the real prompts. This is the first-class-function
DI the design template favors.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import replace
from typing import TYPE_CHECKING

import click

from sempipe.cli.completions import complete_chat_models, complete_embed_models
from sempipe.config.display import render_show, settings_with_origin
from sempipe.config.paths import config_path, human_path
from sempipe.config.store import Config, load_config, save_config
from sempipe.container import build_container
from sempipe.core.errors import SetupFault
from sempipe.models.base import parse_model_ref

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable

__all__ = ["config_command", "run_interactive_setup"]

_TRY_IT = 'echo "hello world" | sempipe map "translate to Spanish"'
_NON_TTY = (
    "error: 'sempipe config' is interactive and needs a terminal\n"
    "  Set a model without prompts:\n"
    "    sempipe config model ollama/qwen3:8b        (local, free)\n"
    "    sempipe config model gpt-4o-mini            (cloud)"
)


@click.group(name="config", invoke_without_command=True)
@click.pass_context
def config_command(ctx: click.Context) -> None:
    """Configure models and settings."""
    if ctx.invoked_subcommand is not None:
        return
    import sys

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SetupFault(_NON_TTY)
    asyncio.run(_interactive_entry())


@config_command.command(name="show")
def config_show() -> None:
    """Show the effective settings and where each comes from."""
    env = os.environ
    path = config_path(env)
    config = load_config(path)
    click.echo(render_show(settings_with_origin(env, config), human_path(path)))


@config_command.command(name="model")
@click.argument("model_string", shell_complete=complete_chat_models)
def config_set_model(model_string: str) -> None:
    """Set the default chat model (e.g. ollama/qwen3:8b, gpt-4o-mini)."""
    ref = parse_model_ref(model_string)
    _update(lambda c: replace(c, model=str(ref)))
    click.echo(f"model set to {ref}")


@config_command.command(name="embed-model")
@click.argument("model_string", shell_complete=complete_embed_models)
def config_set_embed_model(model_string: str) -> None:
    """Set the embedding model used by embed and top_k."""
    ref = parse_model_ref(model_string)
    _update(lambda c: replace(c, embed_model=str(ref)))
    click.echo(f"embed-model set to {ref}")


def _update(change: Callable[[Config], Config]) -> None:
    path = config_path(os.environ)
    save_config(path, change(load_config(path)))


async def _interactive_entry() -> None:
    async with build_container(os.environ) as container:
        path = config_path(os.environ)
        await run_interactive_setup(
            current=container.config,
            probe=container.probe_ollama,
            ask=lambda question, default: click.prompt(question, default=default),
            confirm=lambda question: click.confirm(question, default=True),
            say=click.echo,
            save=lambda config: save_config(path, config),
        )


async def run_interactive_setup(
    *,
    current: Config,
    probe: Callable[[], Awaitable[tuple[str, ...] | None]],
    ask: Callable[[str, str], str],
    confirm: Callable[[str], bool],
    say: Callable[[str], None],
    save: Callable[[Config], None],
) -> Config:
    say("sempipe setup — one minute, three questions\n")
    names = await probe() or ()
    chat = _first_chat(names)
    if chat is not None:
        say(f"  ✓ found Ollama ({len(names)} models)\n")
        model_answer = ask("Default model?", f"ollama/{chat}")
    else:
        # No Ollama, or Ollama has only embedding models — offer a cloud chat model.
        say(
            "  no local chat model found — install one at https://ollama.com, "
            "or use a cloud model.\n"
        )
        model_answer = ask("Default model (e.g. gpt-4o-mini, needs OPENAI_API_KEY)", "gpt-4o-mini")
    embed_answer = ask("Embedding model?", f"ollama/{_first_embed(names)}")

    updated = replace(
        current,
        model=str(parse_model_ref(model_answer)),
        embed_model=str(parse_model_ref(embed_answer)),
    )
    if confirm("Save to config?"):
        save(updated)
        say("\n  Saved. Try it:")
        say(f"    {_TRY_IT}")
    else:
        say("\n  Not saved. Set a model any time with: sempipe config model <name>")
    return updated


def _first_chat(names: tuple[str, ...]) -> str | None:
    """The first non-embedding model, or None — never propose an embedding
    model as the chat default just because it's the only thing installed."""
    return next((name for name in names if "embed" not in name.lower()), None)


def _first_embed(names: tuple[str, ...]) -> str:
    return next((name for name in names if "embed" in name.lower()), "nomic-embed-text")
