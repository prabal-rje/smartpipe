"""The ``config`` verb: show settings, set values, or run the provider-first picker.

Bare ``smartpipe config`` is an opencode-style three-phase picker: detect what's
connected (env keys, ChatGPT login, a local Ollama), fetch the chosen provider's
live catalog (cached for a day; failures degrade to typed input), then pick —
arrow keys on a real terminal, the numbered/typed prompt everywhere else.
All I/O arrives as injected callables (choose/ask/confirm/say/save) so the whole
flow is unit-testable without a terminal — the click wiring supplies the real
prompts. This is the first-class-function DI the design template favors.
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
from smartpipe.models.base import ModelRef, parse_model_ref

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from pathlib import Path

    from smartpipe.config.picker import ProbeChip, ProviderStatus

__all__ = ["config_command", "offer_shell_completions", "run_provider_picker"]

_TRY_IT = 'echo "hello world" | smartpipe map "translate to Spanish"'
_NON_TTY = (
    "error: 'smartpipe config' is interactive and needs a terminal\n"
    "  Set a model without prompts:\n"
    "    smartpipe config model ollama/qwen3:8b        (local, free)\n"
    "    smartpipe config model gpt-5.4-mini            (cloud)"
)
_NOT_SAVED = "\n  Not saved. Set a model any time with: smartpipe config model <name>"
_TYPE_IT = "type a model name instead…"

# provider → (example ref, aside) — the typed-input path's paste bait
_EXAMPLES: dict[str, tuple[str, str]] = {
    "ollama": ("ollama/qwen3:8b", "local; bare ollama tag names work too"),
    "openai": ("openai/gpt-5.4-mini", "needs OPENAI_API_KEY or ChatGPT login"),
    "gemini": ("gemini/gemini-3.1-flash-lite", "Google - needs GEMINI_API_KEY"),
    "anthropic": ("anthropic/claude-opus-4-8", "needs ANTHROPIC_API_KEY"),
    "mistral": ("mistral/mistral-small-latest", "needs MISTRAL_API_KEY"),
    "openrouter": ("openrouter/anthropic/claude-sonnet-5", "needs OPENROUTER_API_KEY"),
}


@click.group(name="config", invoke_without_command=True)
@click.pass_context
def config_command(ctx: click.Context) -> None:
    """Configure models and settings."""
    if ctx.invoked_subcommand is not None:
        return
    import sys

    if not (sys.stdin.isatty() and sys.stdout.isatty()):
        raise SetupFault(_NON_TTY)
    asyncio.run(_picker_entry())


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


@config_command.command(name="ocr-model")
@click.argument("model_string")
def config_set_ocr(model_string: str) -> None:
    """Set the document parsing model (e.g. mistral-ocr-latest) for PDFs and images."""
    ref = parse_model_ref(model_string)
    _update(lambda c: replace(c, ocr_model=str(ref)))
    click.echo(f"ocr-model set to {ref} (parses ingested PDFs/images; each use is disclosed)")


@config_command.command(name="media-embed-model")
@click.argument("model_string")
def config_set_media_embed(model_string: str) -> None:
    """Set the joint text+image embedder (e.g. jina/jina-clip-v2) for media items."""
    ref = parse_model_ref(model_string)
    _update(lambda c: replace(c, media_embed_model=str(ref)))
    click.echo(f"media-embed-model set to {ref} (media items embed as pixels in its space)")


@config_command.command(name="cache")
@click.argument("state", type=click.Choice(["on", "off"]))
def config_set_cache(state: str) -> None:
    """Turn result caching on or off (identical calls reuse stored replies)."""
    _update(lambda c: replace(c, cache=state == "on"))
    click.echo(f"cache {state} — stored replies live in ~/.cache/smartpipe (smartpipe cache clear)")


_UPDATE_CHECK_SAID = {
    "on": "update-check on — smartpipe asks PyPI for the latest release once a day",
    "off": "update-check off — no release checks, no notices (undo: config update-check on)",
}


@config_command.command(name="update-check")
@click.argument("state", type=click.Choice(["on", "off"]))
def config_set_update_check(state: str) -> None:
    """Turn the daily release check on or off (off also silences the notice)."""
    _update(lambda c: replace(c, update_check=state == "on"))
    click.echo(_UPDATE_CHECK_SAID[state])


@config_command.command(name="media-previews")
@click.argument("state", type=click.Choice(["on", "off"]))
def config_set_media_previews(state: str) -> None:
    """Turn terminal media previews on or off (thumbnails, waveforms, play links)."""
    _update(lambda c: replace(c, media_previews=state == "on"))
    detail = (
        "media items render thumbnails/waveforms at the terminal"
        if state == "on"
        else "media items show only their summary line"
    )
    click.echo(f"media-previews {state} — {detail}")


def _update(change: Callable[[Config], Config]) -> None:
    path = config_path(os.environ)
    save_config(path, change(load_config(path)))


# --- the provider-first picker (bare `smartpipe config`) --------------------------------


async def _picker_entry() -> None:
    import sys
    import time
    from pathlib import Path

    from smartpipe.config.picker import cache_day
    from smartpipe.config.state_cache import (
        catalog_path,
        load_catalog,
        load_probe_chips,
        probe_path,
        store_catalog,
    )
    from smartpipe.container import build_container
    from smartpipe.io.arrow_menu import arrow_choose, menu_capable, numbered_choose

    async with build_container(os.environ) as container:
        path = config_path(os.environ)
        env = container.env
        now = time.time()

        async def fetch(provider: str) -> tuple[str, ...] | None:
            location = catalog_path(env, provider, cache_day(now))
            cached = load_catalog(location)
            if cached is not None:
                return cached  # today's file is fresh — skip the network
            from smartpipe.models.catalogs import fetch_catalog

            names = await fetch_catalog(provider, env, container.http_client)
            if names:
                store_catalog(location, names)
            return names

        def login() -> bool:
            from smartpipe.config.credentials import credentials_path, load_oauth

            return load_oauth(credentials_path(env), "openai") is not None

        def ask(question: str, default: str) -> str:
            return str(click.prompt(question, default=default))

        def choose(title: str, labels: tuple[str, ...], start: int) -> int | None:
            if menu_capable(
                stdin_tty=sys.stdin.isatty(),
                stdout_tty=sys.stdout.isatty(),
                term=os.environ.get("TERM"),
            ):
                return arrow_choose(title, labels, sys.stdout, start=start)
            return numbered_choose(title, labels, start, ask=ask, say=click.echo)

        await run_provider_picker(
            current=container.config,
            env=env,
            probe=container.probe_ollama,
            login=login,
            fetch_catalog=fetch,
            chips=load_probe_chips(probe_path(env)),
            now=now,
            choose=choose,
            ask=ask,
            confirm=lambda question, default: click.confirm(question, default=default),
            say=click.echo,
            save=lambda config: save_config(path, config),
            offer_completions=lambda: offer_shell_completions(
                env=os.environ,
                home=Path.home(),
                confirm=lambda question: click.confirm(question, default=True),
                say=click.echo,
            ),
        )


async def run_provider_picker(
    *,
    current: Config,
    env: Mapping[str, str],
    probe: Callable[[], Awaitable[tuple[str, ...] | None]],
    login: Callable[[], bool],
    fetch_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    chips: Mapping[str, ProbeChip],
    now: float,
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    confirm: Callable[[str, bool], bool],
    say: Callable[[str], None],
    save: Callable[[Config], None],
    offer_completions: Callable[[], None] | None = None,
) -> Config:
    """Detect → catalog → pick, then the two smoothness dials (embed pairing,
    the backup-model question) and one save at the end."""
    from smartpipe.cli.screens import good, heading, tint
    from smartpipe.config.picker import (
        detect_providers,
        embed_pair_allowed,
        has_jina_key,
        paired_embed,
    )

    say(heading("smartpipe setup") + tint(" — pick a provider, pick a model", "2") + "\n")
    tags = await probe()
    statuses = detect_providers(env, ollama_tags=tags, openai_login=login())
    detected = tuple(status for status in statuses if status.detected)
    for line in _connect_block(statuses, jina=has_jina_key(env)):
        say(line)
    if not detected:
        say("  Connect one, then rerun: smartpipe config")
        return current
    provider_labels = tuple(f"{status.provider:<12}{status.detail}" for status in detected)
    index = choose("Pick a provider:", provider_labels, 0)
    if index is None:
        say(_NOT_SAVED)
        return current
    provider = detected[index].provider
    model = await _pick_model(
        provider,
        question="Default model?",
        tags=tags,
        fetch_catalog=fetch_catalog,
        chips=chips,
        now=now,
        choose=choose,
        ask=ask,
        say=say,
    )
    if model is None:
        say(_NOT_SAVED)
        return current
    updated = replace(current, model=model)
    paired = paired_embed(provider, tags)
    pair_stamped = paired is not None and embed_pair_allowed(current.embed_model)
    if pair_stamped:
        updated = replace(updated, embed_model=paired)
    if confirm("Add a backup model for provider outages?", False):
        fallback = await _pick_fallback(
            detected,
            provider_labels,
            tags=tags,
            fetch_catalog=fetch_catalog,
            chips=chips,
            now=now,
            choose=choose,
            ask=ask,
            say=say,
        )
        if fallback is not None:
            updated = replace(updated, fallback_model=fallback)
    if not confirm("Save to config?", True):
        say(_NOT_SAVED)
        if offer_completions is not None:
            offer_completions()
        return updated
    save(updated)
    say("")
    say("  " + good("✓") + f" model {updated.model}")
    if pair_stamped:
        say("  " + good("✓") + f" embed-model {paired}" + tint(f"  (paired with {provider})", "2"))
    if updated.fallback_model is not None and updated.fallback_model != current.fallback_model:
        say(
            "  "
            + good("✓")
            + f" fallback-model {updated.fallback_model}"
            + tint("  (switches in when the provider looks down)", "2")
        )
    # completions BEFORE the try-it invitation: printing a paste-me command
    # while questions remain baits the paste into the next prompt (owner-hit)
    if offer_completions is not None:
        offer_completions()
    say("\n  " + good("Saved.") + " Try it:")
    say("    " + tint(_TRY_IT, "36"))
    return updated


def _connect_block(statuses: tuple[ProviderStatus, ...], *, jina: bool) -> tuple[str, ...]:
    """The detection screen around the menu: undetected providers listed dim with
    HOW to connect (the export line to run — smartpipe never prompts for keys)."""
    from smartpipe.cli.screens import heading, tint

    undetected = tuple(status for status in statuses if not status.detected)
    lines: list[str] = []
    if len(undetected) == len(statuses):
        lines.append(heading("No providers connected yet") + tint(" — connect one:", "2"))
        lines.extend(
            tint(f"    {status.provider:<12}{status.connect_hint}", "2") for status in statuses
        )
        return tuple(lines)
    if undetected:
        lines.append(tint("  not connected (how to connect):", "2"))
        lines.extend(
            tint(f"    {status.provider:<12}{status.connect_hint}", "2") for status in undetected
        )
    if jina:
        lines.append(tint("    jina        JINA_API_KEY set — embeddings only (embed/top_k)", "2"))
    if lines:
        lines.append("")
    return tuple(lines)


async def _pick_model(
    provider: str,
    *,
    question: str,
    tags: tuple[str, ...] | None,
    fetch_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    chips: Mapping[str, ProbeChip],
    now: float,
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
) -> str | None:
    """Phase 2 + 3 for one provider: live catalog (or local tags) → menu; any
    fetch failure degrades to the typed-input path. None = the user backed out."""
    from smartpipe.config.picker import (
        capped_catalog,
        model_labels,
        ollama_chat_tags,
        preferred_index,
    )

    names = ollama_chat_tags(tags or ()) if provider == "ollama" else await fetch_catalog(provider)
    if not names:
        note = (
            "no local chat model found — pull one, e.g.: ollama pull qwen3:8b"
            if provider == "ollama"
            else "couldn't fetch the live catalog — type the model name instead."
        )
        return _typed_model(provider, question=question, ask=ask, say=say, note=note)
    shown, hidden = capped_catalog(names)
    type_it = _TYPE_IT if not hidden else f"{_TYPE_IT} ({hidden} more not shown)"
    labels = (*model_labels(provider, shown, chips, now), type_it)
    start = preferred_index(shown) if provider == "ollama" else 0
    picked = choose(f"Pick a model ({provider}):", labels, start)
    if picked is None:
        return None
    if picked == len(labels) - 1:
        return _typed_model(provider, question=question, ask=ask, say=say, note=None)
    return str(parse_model_ref(f"{provider}/{shown[picked]}"))


def _typed_model(
    provider: str,
    *,
    question: str,
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
    note: str | None,
) -> str:
    """The typed-input path, with the provider-prefixed example the wizard shows."""
    from smartpipe.cli.screens import good, tint

    if note is not None:
        say(tint(f"  {note}", "2"))
    example, aside = _EXAMPLES[provider]
    say(tint("  Model names are provider/name:", "2"))
    say("    " + good(example) + tint(f"  ({aside})", "2"))
    say(tint("  Tip: pick one that can SEE images — smartpipe is multimodal;", "2"))
    say(tint("  text-only models refuse image rows.", "2"))
    answer = ask(question, example)
    return str(_parsed_or_reprompt(answer, ask, question))


async def _pick_fallback(
    detected: tuple[ProviderStatus, ...],
    provider_labels: tuple[str, ...],
    *,
    tags: tuple[str, ...] | None,
    fetch_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    chips: Mapping[str, ProbeChip],
    now: float,
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
) -> str | None:
    """One more loop of the picker for the breaker-failover model (item 11)."""
    index = choose("Pick a backup provider:", provider_labels, 0)
    if index is None:
        return None
    fallback = await _pick_model(
        detected[index].provider,
        question="Backup model?",
        tags=tags,
        fetch_catalog=fetch_catalog,
        chips=chips,
        now=now,
        choose=choose,
        ask=ask,
        say=say,
    )
    if fallback is None:
        return None
    if _embeds(fallback):
        say(f"  '{fallback}' embeds — a backup must chat; skipping the backup")
        return None
    return fallback


def _embeds(model: str) -> bool:
    """Embed-only providers, or a name that says so — a fallback must chat
    (mixed-embedder vectors are geometrically meaningless; same rule as the
    container's resolution-time refusal)."""
    ref = parse_model_ref(model)
    return ref.provider in ("local", "jina") or "embed" in ref.name.lower()


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


def _parsed_or_reprompt(answer: str, ask: Callable[[str, str], str], question: str) -> ModelRef:
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
