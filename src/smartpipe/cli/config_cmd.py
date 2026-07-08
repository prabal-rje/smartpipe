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

from smartpipe.cli.completions import complete_chat_models, complete_embed_models
from smartpipe.config.display import render_show, settings_with_origin
from smartpipe.config.paths import config_path, human_path
from smartpipe.config.store import Config, load_config, save_config
from smartpipe.core.errors import SetupFault
from smartpipe.io import diagnostics
from smartpipe.models.base import parse_model_ref

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from pathlib import Path

__all__ = ["config_command", "offer_shell_completions", "run_interactive_setup"]

_TRY_IT = 'echo "hello world" | smartpipe map "translate to Spanish"'
_NON_TTY = (
    "error: 'smartpipe config' is interactive and needs a terminal\n"
    "  Set a model without prompts:\n"
    "    smartpipe config model ollama/qwen3:8b        (local, free)\n"
    "    smartpipe config model gpt-5.4-mini            (cloud)"
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


@config_command.command(name="profile")
@click.argument("name", required=False)
@click.option("--unset", is_flag=True, help="Clear the active profile.")
def config_profile(name: str | None, unset: bool) -> None:
    """Show, switch, or clear the active profile.

    \b
      smartpipe config profile           list profiles (the active one marked)
      smartpipe config profile local     switch — local runs ollama/gemma-4-e2b
      SMARTPIPE_PROFILE=gemini smartpipe … one-off, no file change
    """
    from smartpipe.config.store import profile_names, set_active_profile

    path = config_path(os.environ)
    if unset:
        set_active_profile(path, None)
        diagnostics.note("profile cleared — flat config keys stand alone")
        return
    if name is None:
        config = load_config(path, os.environ)
        for known in profile_names(path):
            marker = "* " if known == config.profile else "  "
            click.echo(f"{marker}{known}")
        return
    known = profile_names(path)
    if name not in known:
        raise SetupFault(
            f"error: profile {name!r} doesn't exist\n  Known profiles: {', '.join(known)}\n"
            f"  Define [profiles.{name}] in {human_path(path)} to create it."
        )
    set_active_profile(path, name)
    effective = load_config(path)
    summary = ", ".join(
        f"{label} {value}"
        for label, value in (("model:", effective.model), ("embed:", effective.embed_model))
        if value is not None
    )
    diagnostics.note(f"profile '{name}' active — {summary}")


@config_command.command(name="show")
def config_show() -> None:
    """Show the effective settings and where each comes from."""
    env = os.environ
    path = config_path(env)
    config = load_config(path, env)
    if config.profile is not None:
        origin = "SMARTPIPE_PROFILE" if env.get("SMARTPIPE_PROFILE", "").strip() else "config file"
        click.echo(f"profile      {config.profile}  ({origin})")
    click.echo(render_show(settings_with_origin(env, config), human_path(path)))


@config_command.command(name="model")
@click.argument("model_string", shell_complete=complete_chat_models)
def config_set_model(model_string: str) -> None:
    """Set the default chat model (e.g. ollama/qwen3:8b, gpt-5.4-mini)."""
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


@config_command.command(name="stt-model")
@click.argument("model_string")
def config_set_stt(model_string: str) -> None:
    """Set the remote transcription model (e.g. openai/whisper-1) — verbatim STT."""
    ref = parse_model_ref(model_string)
    _update(lambda c: replace(c, stt_model=str(ref)))
    click.echo(f"stt-model set to {ref} (runs first in the audio ladder; consent rules apply)")


@config_command.command(name="cache")
@click.argument("state", type=click.Choice(["on", "off"]))
def config_set_cache(state: str) -> None:
    """Turn result caching on or off (identical calls reuse stored replies)."""
    _update(lambda c: replace(c, cache=state == "on"))
    click.echo(f"cache {state} — stored replies live in ~/.cache/smartpipe (smartpipe cache clear)")


@config_command.command(name="update-check")
@click.argument("state", type=click.Choice(["on", "off"]))
def config_set_update_check(state: str) -> None:
    """Turn the daily release check on or off (off also silences the notice)."""
    _update(lambda c: replace(c, update_check=state == "on"))
    click.echo(f"update-check {state} — smartpipe asks PyPI for the latest release once a day")


def _update(change: Callable[[Config], Config]) -> None:
    path = config_path(os.environ)
    save_config(path, change(load_config(path)))


async def _interactive_entry() -> None:
    from pathlib import Path

    from smartpipe.container import build_container

    async with build_container(os.environ) as container:
        path = config_path(os.environ)

        def confirm(question: str) -> bool:
            return click.confirm(question, default=True)

        await run_interactive_setup(
            current=container.config,
            probe=container.probe_ollama,
            ask=lambda question, default: click.prompt(question, default=default),
            confirm=confirm,
            say=click.echo,
            save=lambda config: save_config(path, config),
            offer_completions=lambda: offer_shell_completions(
                env=os.environ, home=Path.home(), confirm=confirm, say=click.echo
            ),
        )


async def run_interactive_setup(
    *,
    current: Config,
    probe: Callable[[], Awaitable[tuple[str, ...] | None]],
    ask: Callable[[str, str], str],
    confirm: Callable[[str], bool],
    say: Callable[[str], None],
    save: Callable[[Config], None],
    offer_completions: Callable[[], None] | None = None,
) -> Config:
    from smartpipe.cli.screens import good, heading, tint

    say(heading("smartpipe setup") + tint(" — one minute, three questions", "2") + "\n")
    if current.profile is None and current.model is None:
        say(
            heading("Pick a starting profile")
            + tint(" (a named bundle you can switch any time):", "2")
        )
        say(
            "  1. openai — gpt-5.4-mini + text-embedding-3-small "
            "(key or ChatGPT login; no audio input)"
        )
        say("  2. gemini — gemini-3.1-flash-lite, the most multimodal wire (needs GEMINI_API_KEY)")
        say("  3. local  — ollama/gemma-4-e2b, multimodal, nothing leaves this machine")
        say("  4. custom — answer the questions instead")
        choice = ask("Profile [1-4]?", "4").strip()
        picked = {"1": "openai", "2": "gemini", "3": "local"}.get(choice)
        if picked is not None:
            from smartpipe.config.store import BUILTIN_PROFILES

            chosen = replace(current, profile=picked)
            save(chosen)  # a fresh setup has no flat keys to materialize
            bundle = ", ".join(f"{k} = {v}" for k, v in BUILTIN_PROFILES[picked].items())
            say("\n  " + good("✓") + f" profile '{picked}' active " + tint(f"({bundle})", "2"))
            if BUILTIN_PROFILES[picked].get("allow-captions"):
                say(
                    "  note: this profile converts images/audio to text through its"
                    " model when needed (fractions of a cent each, disclosed per row)"
                )
            say("  Check the setup end to end:  smartpipe doctor\n")
            if offer_completions is not None:
                offer_completions()
            return chosen
    names = await probe() or ()
    chat = _first_chat(names)
    say(tint("  Model names are provider/name:", "2"))
    say(
        "    "
        + good("openai/gpt-5.4-mini")
        + tint("  (needs OPENAI_API_KEY or ChatGPT login)", "2")
    )
    say(
        "    "
        + good("gemini/gemini-3.1-flash-lite")
        + tint("  (Google - needs GEMINI_API_KEY)", "2")
    )
    say("    " + good("ollama/llava") + tint("  (local; bare ollama tag names work too)", "2"))
    say(tint("  Tip: pick one that can SEE images — smartpipe is multimodal;", "2"))
    say(tint("  text-only models refuse image rows.", "2"))
    if chat is not None:
        local_menu = [n for n in names if "embed" not in n.lower()][:8]
        say("  " + good("✓") + f" found Ollama ({len(names)} models). You can type any of:")
        say(tint("    " + " · ".join(local_menu), "36"))
        say("")
        model_answer = ask("Default model?", f"ollama/{chat}")
    else:
        # No Ollama, or Ollama has only embedding models — offer a cloud chat model.
        say(
            tint(
                "  no local chat model found — install one at https://ollama.com, "
                "or use a cloud model.",
                "2",
            )
            + "\n"
        )
        model_answer = ask("Default model?", "openai/gpt-5.4-mini")
    embed_answer = ask("Embedding model?", f"ollama/{_first_embed(names)}")

    model_ref = _parsed_or_reprompt(model_answer, ask, "Default model?")
    embed_ref = _parsed_or_reprompt(embed_answer, ask, "Embedding model?")
    updated = replace(current, model=str(model_ref), embed_model=str(embed_ref))
    if confirm("Save to config?"):
        save(updated)
        saved = True
    else:
        say("\n  Not saved. Set a model any time with: smartpipe config model <name>")
        saved = False
    # completions BEFORE the try-it invitation: printing a paste-me command
    # while questions remain baits the paste into the next prompt (owner-hit)
    if offer_completions is not None:
        offer_completions()
    if saved:
        say("\n  " + good("Saved.") + " Try it:")
        say("    " + tint(_TRY_IT, "36"))
    return updated


_RC_BY_SHELL: dict[str, tuple[str, str]] = {
    "zsh": (".zshrc", 'eval "$(_SMARTPIPE_COMPLETE=zsh_source smartpipe)"'),
    "bash": (".bashrc", 'eval "$(_SMARTPIPE_COMPLETE=bash_source smartpipe)"'),
}


def offer_shell_completions(
    *,
    env: Mapping[str, str],
    home: Path,
    confirm: Callable[[str], bool],
    say: Callable[[str], None],
) -> None:
    """The wizard's parting offer (default yes): append the completion eval
    line to the shell's rc file — idempotent (already installed: silent),
    disclosed (says what it wrote where), declinable (points at the manual
    instructions). Unknown shells get no offer; guessing at rc files is worse
    than not asking."""
    shell = env.get("SHELL", "").rsplit("/", 1)[-1]
    known = _RC_BY_SHELL.get(shell)
    if known is None:
        return
    rc_name, line = known
    rc = home / rc_name
    existing = rc.read_text(encoding="utf-8") if rc.exists() else ""
    if line in existing:
        return  # installed on a previous run — don't nag
    if not confirm(f"Install {shell} tab completion into {rc_name}?"):
        say("  Skipped. Manual instructions: docs/troubleshooting.md")
        return
    prefix = "" if not existing or existing.endswith("\n") else "\n"
    with rc.open("a", encoding="utf-8") as handle:
        handle.write(f"{prefix}{line}\n")
    say(f"  ✓ added to {rc}: {line}")


_PREFERRED_FAMILIES = ("llava", "gemma", "qwen", "llama", "mistral", "phi", "kimi", "glm")


def _first_chat(names: tuple[str, ...]) -> str | None:
    """A sensible chat default from ollama's list: prefer known families
    (vision-capable first), never an embedding model. ':cloud' passthrough
    tags compete as equals — they are affordable frontier models, and
    penalizing them while suggesting openai/ would be incoherent (owner
    ruling)."""
    candidates = [n for n in names if "embed" not in n.lower()]
    for family in _PREFERRED_FAMILIES:
        for name in candidates:
            if family in name.lower():
                return name
    return candidates[0] if candidates else None


def _parsed_or_reprompt(answer: str, ask: Callable[[str, str], str], question: str) -> object:
    """Validate a wizard answer NOW — a saved typo ('\\', stray paste) otherwise
    surfaces later as a confusing provider 400 (owner-hit). Two strikes, then
    UsageFault with the config-command escape hatch."""
    from smartpipe.core.errors import UsageFault

    for attempt in range(2):
        try:
            return parse_model_ref(answer.strip())
        except UsageFault as fault:
            if attempt == 1:
                raise
            first = str(fault).splitlines()[0]
            answer = ask(f"{question} (that wasn't a model ref: {first})", "")
    raise AssertionError("unreachable")  # pragma: no cover


def _first_embed(names: tuple[str, ...]) -> str:
    return next((name for name in names if "embed" in name.lower()), "nomic-embed-text")
