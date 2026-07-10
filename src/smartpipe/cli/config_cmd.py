"""The ``config`` verb: show settings, set values, or run the three-stage flow.

Bare ``smartpipe config`` is a SEQUENTIAL three-stage flow (owner-ruled):
TEXT → EMBED → OCR. Each stage arrow-picks a provider from ALL providers
(connected ones badged; picking an unconnected one drops inline into the
``auth login`` connect flow and continues seamlessly), then that category's
model from the live catalog. Re-runs preselect the existing choices and
restamp only changes; every stage is skippable; Ctrl-C anywhere leaves the
prior config untouched (one atomic save at the very end).
All I/O arrives as injected callables (choose/ask/confirm/say/save/connect)
so the whole flow is unit-testable without a terminal — the click wiring
supplies the real prompts. This is the first-class-function DI the design
template favors.
"""

from __future__ import annotations

import asyncio
import os
from dataclasses import dataclass, replace
from enum import Enum
from typing import TYPE_CHECKING, assert_never

import click

from smartpipe.cli.completions import complete_chat_models, complete_embed_models
from smartpipe.config.display import render_show, settings_with_origin
from smartpipe.config.paths import config_path, human_path
from smartpipe.config.store import Config, load_config, save_config
from smartpipe.core.errors import SetupFault
from smartpipe.io import diagnostics, tty
from smartpipe.models.base import ModelRef, parse_model_ref

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping
    from pathlib import Path

    from smartpipe.config.picker import ChipSources, StageEntry

__all__ = ["config_command", "offer_shell_completions", "run_config_flow"]

_TRY_IT = 'echo "hello world" | smartpipe map "translate to Spanish"'
_NON_TTY = (
    "error: 'smartpipe config' is interactive and needs a terminal\n"
    "  Set a model without prompts:\n"
    "    smartpipe config model ollama/qwen3:8b        (local, free)\n"
    "    smartpipe config model gpt-5.4-mini            (cloud)"
)
_NOT_SAVED = "\n  Not saved. Set a model any time with: smartpipe config model <name>"
_TYPE_IT = "type a model name instead…"
_BACK_ANSWERS = frozenset(("back", "b", "no", "n"))

# provider → (example ref, aside) — the typed-input path's paste bait
_EXAMPLES: dict[str, tuple[str, str]] = {
    "ollama": ("ollama/qwen3:8b", "local; bare ollama tag names work too"),
    "openai": ("openai/gpt-5.4-mini", "needs OPENAI_API_KEY or ChatGPT login"),
    "gemini": ("gemini/gemini-3.1-flash-lite", "Google - needs GEMINI_API_KEY"),
    "anthropic": ("anthropic/claude-opus-4-8", "needs ANTHROPIC_API_KEY"),
    "mistral": ("mistral/mistral-small-latest", "needs MISTRAL_API_KEY"),
    "openrouter": ("openrouter/anthropic/claude-sonnet-5", "needs OPENROUTER_API_KEY"),
}

_EMBED_EXAMPLES: dict[str, tuple[str, str]] = {
    "local": ("local/nomic-embed-text-v1.5", "on-device, free"),
    "ollama": ("ollama/embeddinggemma", "local; pull it first: ollama pull embeddinggemma"),
    "openai": ("openai/text-embedding-3-small", "needs OPENAI_API_KEY"),
    "gemini": ("gemini/gemini-embedding-001", "needs GEMINI_API_KEY"),
    "mistral": ("mistral/mistral-embed", "needs MISTRAL_API_KEY"),
    "jina": ("jina/jina-clip-v2", "embeds text AND images in one space"),
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
    click.echo(
        render_show(
            settings_with_origin(env, config),
            human_path(path),
            color=tty.stdout_supports_color(),
        )
    )


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


# --- the three-stage flow (bare `smartpipe config`) ---------------------------------------


async def _picker_entry() -> None:  # pragma: no cover — terminal wiring; the flow is tested
    import sys
    import time
    from functools import partial
    from importlib.util import find_spec
    from pathlib import Path

    from smartpipe.config.picker import ChipSources, RegistryCaps, cache_day
    from smartpipe.config.state_cache import (
        catalog_path,
        load_catalog,
        load_probe_chips,
        load_registry,
        probe_path,
        registry_path,
        store_catalog,
        store_registry,
    )
    from smartpipe.container import build_container
    from smartpipe.io.arrow_menu import arrow_choose, menu_capable, numbered_choose

    async with build_container(os.environ) as container:
        path = config_path(os.environ)
        session_env = dict(container.env)  # inline connects add keys here mid-flow
        now = time.time()

        async def capability_registry() -> dict[str, RegistryCaps]:
            location = registry_path(session_env, cache_day(now))
            cached = load_registry(location)
            if cached is not None:
                return cached  # today's snapshot is fresh — skip the network
            from smartpipe.models.catalogs import fetch_registry

            fetched = await fetch_registry(session_env, container.http_client)
            if fetched:
                store_registry(location, fetched)
            return fetched or {}  # graceful absent: no registry = no registry chips

        declared: dict[str, tuple[str, ...]] = {}
        if container.config.model is not None and container.config.model_capabilities is not None:
            declared[str(parse_model_ref(container.config.model))] = (
                container.config.model_capabilities
            )
        sources = ChipSources(
            probed=load_probe_chips(probe_path(session_env)),
            registry=await capability_registry(),
            declared=declared,
        )

        async def cached_fetch(
            kind: str,
            fetcher: Callable[..., Awaitable[tuple[str, ...] | None]],
            provider: str,
        ) -> tuple[str, ...] | None:
            location = catalog_path(session_env, f"{provider}{kind}", cache_day(now))
            cached = load_catalog(location)
            if cached is not None:
                return cached  # today's file is fresh — skip the network
            names = await fetcher(provider, session_env, container.http_client)
            if names:
                store_catalog(location, names)
            return names

        async def fetch(provider: str) -> tuple[str, ...] | None:
            from smartpipe.models.catalogs import fetch_catalog

            return await cached_fetch("", fetch_catalog, provider)

        async def fetch_embed(provider: str) -> tuple[str, ...] | None:
            from smartpipe.models.catalogs import fetch_embed_catalog

            return await cached_fetch("-embed", fetch_embed_catalog, provider)

        def login() -> bool:
            from smartpipe.config.credentials import credentials_path, load_oauth

            return load_oauth(credentials_path(session_env), "openai") is not None

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

        async def connect(entry: StageEntry) -> bool:
            return await _connect_inline(entry, session_env, choose=choose, say=click.echo)

        async def verify(config: Config) -> None:
            await _verify_live(
                config,
                session_env,
                now=now,
                confirm=lambda question, default: click.confirm(question, default=default),
                say=click.echo,
            )

        await run_config_flow(
            current=container.config,
            env=session_env,
            probe=container.probe_ollama,
            login=login,
            fetch_catalog=fetch,
            fetch_embed_catalog=fetch_embed,
            chips=sources,
            now=now,
            choose=choose,
            ask=ask,
            confirm=lambda question, default: click.confirm(question, default=default),
            say=click.echo,
            save=partial(save_config, path),
            connect=connect,
            local_embed_available=find_spec("fastembed") is not None,
            run_verify=verify,
            offer_completions=lambda: offer_shell_completions(
                env=os.environ,
                home=Path.home(),
                confirm=lambda question: click.confirm(question, default=True),
                say=click.echo,
            ),
        )


async def _verify_live(
    config: Config,
    session_env: dict[str, str],
    *,
    now: float,
    confirm: Callable[[str, bool], bool],
    say: Callable[[str], None],
) -> None:  # pragma: no cover — the live probe path, pragma'd like doctor --probe
    from importlib import resources

    from smartpipe.config.state_cache import load_probe_chips, probe_path, record_probe
    from smartpipe.config.verify import VerifyReport, probe_models, run_exit_probe
    from smartpipe.container import build_container

    def asset(name: str) -> bytes:
        return (resources.files("smartpipe.assets") / name).read_bytes()

    async def live() -> VerifyReport:
        # a fresh container: it re-reads the key store, so an inline connect
        # made seconds ago reaches these wires too
        async with build_container(session_env) as container:
            chat = await container.chat_model(config.model) if config.model else None
            embed = (
                await container.embedding_model(config.embed_model) if config.embed_model else None
            )
            return await probe_models(chat, embed, asset)

    location = probe_path(session_env)
    await run_exit_probe(
        chat_ref=config.model,
        embed_ref=config.embed_model,
        chips=load_probe_chips(location),
        now=now,
        confirm=confirm,
        say=say,
        probe=live,
        record=lambda ref, sees, hears: record_probe(
            location, ref, sees=sees, hears=hears, now=now
        ),
    )


async def _connect_inline(
    entry: StageEntry,
    session_env: dict[str, str],
    *,
    choose: Callable[[str, tuple[str, ...], int], int | None],
    say: Callable[[str], None],
) -> bool:  # pragma: no cover — terminal + network wiring; the flow logic is tested
    """Deliverable-1's connect flow, dropped inline into a stage menu."""
    if entry.wire == "local":
        say("  install from https://ollama.com, then: ollama serve — and rerun smartpipe config")
        return False
    if entry.wire == "oauth":
        from smartpipe.cli.auth_cmd import login_dispatch
        from smartpipe.config.credentials import credentials_path, load_oauth

        await login_dispatch("openai", headless=False)
        return load_oauth(credentials_path(session_env), "openai") is not None
    from functools import partial

    from smartpipe.cli.auth_cmd import secret_prompt
    from smartpipe.config.authflow import auth_entry, connect_api_key
    from smartpipe.config.credentials import (
        keys_path,
        overlay_stored_keys,
        save_api_key,
        stored_api_keys,
    )
    from smartpipe.models.http_support import make_client
    from smartpipe.models.keycheck import check_api_key

    door = auth_entry("openai-api" if entry.provider == "openai" else entry.provider)
    assert door is not None, entry.provider
    store_path = keys_path(session_env)
    async with make_client() as client:
        stored = await connect_api_key(
            door,
            secret=secret_prompt,
            choose=choose,
            say=say,
            check=partial(check_api_key, door.provider, env=session_env, client=client),
            store=partial(save_api_key, store_path, door.provider),
            store_display=human_path(store_path),
        )
    if stored:  # pull the new key into this session without re-handling it
        session_env.update(overlay_stored_keys(session_env, stored_api_keys(store_path)))
    return stored


@dataclass(frozen=True, slots=True)
class _OcrChoice:
    value: str | None  # the new setting; None + changed=True means "unset"
    changed: bool


@dataclass(frozen=True, slots=True)
class _TextChoice:
    provider: str | None
    model: str | None


@dataclass(frozen=True, slots=True)
class _EmbedChoice:
    model: str | None
    via_pair: bool


@dataclass(frozen=True, slots=True)
class _Back:
    pass


@dataclass(frozen=True, slots=True)
class _Cancel:
    pass


_BACK = _Back()
_CANCEL = _Cancel()


class _FlowStep(Enum):
    TEXT = "text"
    EMBED = "embed"
    OCR = "ocr"
    REVIEW = "review"


class _ReviewAction(Enum):
    FINISH = "finish"
    BACK = "back"
    DISCARD = "discard"


async def run_config_flow(
    *,
    current: Config,
    env: Mapping[str, str],
    probe: Callable[[], Awaitable[tuple[str, ...] | None]],
    login: Callable[[], bool],
    fetch_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    fetch_embed_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    chips: ChipSources,
    now: float,
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    confirm: Callable[[str, bool], bool],
    say: Callable[[str], None],
    save: Callable[[Config], None],
    connect: Callable[[StageEntry], Awaitable[bool]] | None = None,
    local_embed_available: bool = True,
    run_verify: Callable[[Config], Awaitable[None]] | None = None,
    offer_completions: Callable[[], None] | None = None,
) -> Config:
    """TEXT → EMBED → OCR, then one save, the exit probe, and the try-it screen.

    ``connect`` may mutate ``env`` (an inline key connect adds the provider's
    variable) — the stages re-read it, so badges refresh mid-flow.
    """
    from smartpipe.cli.screens import good, heading, tint

    say(heading("smartpipe setup") + tint(" - text model, then embeddings, then documents", "2"))
    say("")
    tags = await probe()
    updated: Config = current
    embed_base: Config = current
    ocr_base: Config = current
    pair_provider = _provider_of(current.model)
    via_pair = False
    step = _FlowStep.TEXT
    while True:
        match step:
            case _FlowStep.TEXT:
                updated = current
                text = await _stage_text(
                    current_model=current.model,
                    env=env,
                    tags=tags,
                    login=login,
                    fetch_catalog=fetch_catalog,
                    chips=chips,
                    now=now,
                    choose=choose,
                    ask=ask,
                    say=say,
                    connect=connect,
                )
                match text:
                    case _Cancel():
                        say(_NOT_SAVED)
                        return current
                    case _TextChoice(provider=text_provider, model=text_model):
                        if text_model is not None:
                            updated = replace(updated, model=text_model)
                    case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                        assert_never(unreachable)
                # the backup-model question stays right after TEXT (owner-pinned position)
                if updated.model is not None and confirm(
                    "Add a backup model for provider outages?", False
                ):
                    fallback = await _pick_fallback(
                        env,
                        tags=tags,
                        login=login,
                        fetch_catalog=fetch_catalog,
                        chips=chips,
                        now=now,
                        choose=choose,
                        ask=ask,
                        say=say,
                    )
                    if fallback is not None:
                        updated = replace(updated, fallback_model=fallback)
                pair_provider = text_provider or _provider_of(updated.model)
                embed_base = updated
                step = _FlowStep.EMBED
            case _FlowStep.EMBED:
                updated = embed_base
                embed = await _stage_embed(
                    current_embed=updated.embed_model,
                    text_provider=pair_provider,
                    env=env,
                    tags=tags,
                    local_available=local_embed_available,
                    fetch_embed_catalog=fetch_embed_catalog,
                    choose=choose,
                    ask=ask,
                    say=say,
                    connect=connect,
                )
                match embed:
                    case _Back():
                        step = _FlowStep.TEXT
                        continue
                    case _EmbedChoice(model=embed_model, via_pair=paired):
                        via_pair = paired
                        if embed_model is not None:
                            updated = replace(updated, embed_model=embed_model)
                    case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                        assert_never(unreachable)
                ocr_base = updated
                step = _FlowStep.OCR
            case _FlowStep.OCR:
                updated = ocr_base
                ocr = await _stage_ocr(
                    current_ocr=updated.ocr_model,
                    chat_model=updated.model,
                    env=env,
                    choose=choose,
                    ask=ask,
                    say=say,
                    connect=connect,
                )
                match ocr:
                    case _Back():
                        step = _FlowStep.EMBED
                        continue
                    case _OcrChoice(value=value, changed=changed):
                        if changed:
                            updated = replace(updated, ocr_model=value)
                    case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                        assert_never(unreachable)
                step = _FlowStep.REVIEW
            case _FlowStep.REVIEW:
                action = _review_action(changed=updated != current, choose=choose)
                match action:
                    case _ReviewAction.BACK:
                        step = _FlowStep.OCR
                        continue
                    case _ReviewAction.DISCARD:
                        say(_NOT_SAVED)
                        return current
                    case _ReviewAction.FINISH:
                        break
                    case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                        assert_never(unreachable)
                break
            case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
                assert_never(unreachable)

    if updated == current:
        say("")
        say(tint("  nothing changed - config left as it was", "2"))
        if run_verify is not None:
            await run_verify(updated)
        if offer_completions is not None:
            offer_completions()
        say("\n  Try it:")
        say("    " + tint(_TRY_IT, "36"))
        return updated
    save(updated)
    say("")
    if updated.model != current.model:
        say("  " + good("✓") + f" model {updated.model}")
    if updated.embed_model != current.embed_model:
        aside = tint(f"  (paired with {pair_provider})", "2") if via_pair else ""
        say("  " + good("✓") + f" embed-model {updated.embed_model}" + aside)
    if updated.fallback_model != current.fallback_model:
        say(
            "  "
            + good("✓")
            + f" fallback-model {updated.fallback_model}"
            + tint("  (switches in when the provider looks down)", "2")
        )
    if updated.ocr_model != current.ocr_model:
        if updated.ocr_model is None:
            say("  " + good("✓") + " ocr-model unset" + tint("  (built-in local extraction)", "2"))
        else:
            say(
                "  "
                + good("✓")
                + f" ocr-model {updated.ocr_model}"
                + tint("  (documents parse through it at ingestion)", "2")
            )
    if run_verify is not None:
        await run_verify(updated)
    # completions BEFORE the try-it invitation: printing a paste-me command
    # while questions remain baits the paste into the next prompt (owner-hit)
    if offer_completions is not None:
        offer_completions()
    say("\n  " + good("Saved.") + " Try it:")
    say("    " + tint(_TRY_IT, "36"))
    return updated


def _review_action(
    *,
    changed: bool,
    choose: Callable[[str, tuple[str, ...], int], int | None],
) -> _ReviewAction:
    if changed:
        labels = ("save changes", "back - document OCR", "discard changes")
        actions = (_ReviewAction.FINISH, _ReviewAction.BACK, _ReviewAction.DISCARD)
        picked = choose("Review changes:", labels, 0)
    else:
        labels = ("finish - keep current config", "back - document OCR")
        actions = (_ReviewAction.FINISH, _ReviewAction.BACK)
        picked = choose("Review setup:", labels, 0)
    if picked is None:
        return _ReviewAction.DISCARD
    return actions[picked]


def _connect_hint(entry: StageEntry) -> str:
    """The fix line when no inline connect is available - per wire."""
    match entry.wire:
        case "local":
            return "install from https://ollama.com, then: ollama serve"
        case "oauth":
            return "log in first: smartpipe auth login"
        case _:
            return f"connect it first: smartpipe auth login {entry.provider}"


def _provider_of(model: str | None) -> str | None:
    if model is None:
        return None
    from smartpipe.core.errors import UsageFault

    try:
        return parse_model_ref(model).provider
    except UsageFault:
        return None


async def _stage_text(
    *,
    current_model: str | None,
    env: Mapping[str, str],
    tags: tuple[str, ...] | None,
    login: Callable[[], bool],
    fetch_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    chips: ChipSources,
    now: float,
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
    connect: Callable[[StageEntry], Awaitable[bool]] | None,
) -> _TextChoice | _Cancel:
    """Stage 1: a provider/model choice, or terminal setup cancellation."""
    from smartpipe.cli.screens import tint
    from smartpipe.config.picker import stage_labels, text_stage_entries

    while True:
        entries = text_stage_entries(env, ollama_up=tags is not None, login=login())
        rows = list(stage_labels(entries))
        keep_at: int | None = None
        if current_model is not None:
            keep_at = len(rows)
            rows.append(f"keep current: {current_model}")
        skip_at = len(rows)
        rows.append("skip - decide later")
        cancel_at = len(rows)
        rows.append("cancel setup - leave config unchanged")
        start = (
            keep_at
            if keep_at is not None
            else next((i for i, entry in enumerate(entries) if entry.connected), 0)
        )
        picked = choose("Text model - pick a provider:", tuple(rows), start)
        if picked is None or picked == cancel_at:
            return _CANCEL
        if picked in (skip_at, keep_at):
            return _TextChoice(None, None)
        entry = entries[picked]
        if not entry.connected:
            if connect is None:
                say(tint(f"  {_connect_hint(entry)}", "2"))
                return _TextChoice(None, None)
            if not await connect(entry):
                continue  # declined or failed — the menu returns with fresh badges
        model = await _pick_model(
            entry.provider,
            question="Default model?",
            tags=tags,
            fetch_catalog=fetch_catalog,
            chips=chips,
            now=now,
            choose=choose,
            ask=ask,
            say=say,
            preselect=current_model,
        )
        if model is None:
            continue  # backed out of the model menu — provider menu again
        return _TextChoice(entry.provider, model)


async def _stage_embed(
    *,
    current_embed: str | None,
    text_provider: str | None,
    env: Mapping[str, str],
    tags: tuple[str, ...] | None,
    local_available: bool,
    fetch_embed_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
    connect: Callable[[StageEntry], Awaitable[bool]] | None,
) -> _EmbedChoice | _Back:
    """Stage 2: an embed choice, or Back to the Text checkpoint.

    The auto-pair suggestion is the PRESELECTED first option; a deliberate
    earlier choice suppresses it (``embed_pair_allowed``)."""
    from smartpipe.config.picker import (
        embed_pair_allowed,
        embed_stage_entries,
        paired_embed,
        stage_labels,
    )

    pair = paired_embed(text_provider, tags) if text_provider is not None else None
    if pair is not None and (pair == current_embed or not embed_pair_allowed(current_embed)):
        pair = None  # already set, or a deliberate choice stands
    while True:
        entries = embed_stage_entries(
            env, ollama_up=tags is not None, local_available=local_available
        )
        rows: list[str] = []
        pair_at: int | None = None
        if pair is not None:
            pair_at = 0
            rows.append(f"{pair} - paired with {text_provider}")
        providers_at = len(rows)
        rows.extend(stage_labels(entries))
        keep_at: int | None = None
        skip_at: int | None = None
        if current_embed is not None:
            keep_at = len(rows)
            rows.append(f"keep current: {current_embed}")
        else:
            skip_at = len(rows)
            rows.append("skip - keep the built-in default (local, free)")
        back_at = len(rows)
        rows.append("back - text model")
        fallback_start = keep_at if keep_at is not None else skip_at
        start = pair_at if pair_at is not None else fallback_start
        picked = choose(
            "Embedding model - powers embed, top_k, cluster, distinct:",
            tuple(rows),
            start if start is not None else 0,
        )
        if picked is None or picked == back_at:
            return _BACK
        if picked in (keep_at, skip_at):
            return _EmbedChoice(None, False)
        if pair_at is not None and picked == pair_at:
            return _EmbedChoice(pair, True)
        entry = entries[picked - providers_at]
        if not entry.connected and (connect is None or not await connect(entry)):
            if connect is None:
                return _EmbedChoice(None, False)
            continue
        model = await _pick_embed_model(
            entry.provider,
            tags=tags,
            fetch_embed_catalog=fetch_embed_catalog,
            choose=choose,
            ask=ask,
            say=say,
            preselect=current_embed,
        )
        if model is None:
            continue
        return _EmbedChoice(model, False)


async def _pick_embed_model(
    provider: str,
    *,
    tags: tuple[str, ...] | None,
    fetch_embed_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
    preselect: str | None,
) -> str | None:
    """The EMBED stage's model menu: curated lists where no catalog wire exists."""
    from smartpipe.config.picker import (
        JINA_EMBED_MODELS,
        LOCAL_EMBED_MODELS,
        capped_catalog,
        ollama_embed_tags,
    )

    match provider:
        case "local":
            names: tuple[str, ...] = LOCAL_EMBED_MODELS
        case "jina":
            names = JINA_EMBED_MODELS
        case "ollama":
            names = ollama_embed_tags(tags or ())
        case _:
            names = await fetch_embed_catalog(provider) or ()
    if not names:
        note = (
            "no local embedder found - pull one, e.g.: ollama pull embeddinggemma"
            if provider == "ollama"
            else "couldn't fetch the embedding catalog - type the model name instead."
        )
        return _typed_embed(
            provider,
            ask=ask,
            say=say,
            note=note,
            back_to="embedding provider list",
        )
    shown, hidden = capped_catalog(names)
    type_it = _TYPE_IT if not hidden else f"{_TYPE_IT} ({hidden} more not shown)"
    type_at = len(shown)
    labels = (*(f"{provider}/{name}" for name in shown), type_it, "back - embedding provider list")
    start = _preselect_index(provider, shown, preselect)
    while True:
        picked = choose(f"Pick an embedding model ({provider}):", labels, start)
        if picked is None or picked == len(labels) - 1:
            return None
        if picked == type_at:
            typed = _typed_embed(
                provider,
                ask=ask,
                say=say,
                note=None,
                back_to="model list",
            )
            if typed is None:
                continue
            return typed
        return str(parse_model_ref(f"{provider}/{shown[picked]}"))


def _typed_embed(
    provider: str,
    *,
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
    note: str | None,
    back_to: str,
) -> str | None:
    from smartpipe.cli.screens import good, tint

    if note is not None:
        say(tint(f"  {note}", "2"))
    example, aside = _EMBED_EXAMPLES[provider]
    say(tint("  Model names are provider/name:", "2"))
    say("    " + good(example) + tint(f"  ({aside})", "2"))
    say(tint(f"  Type 'back' to return to the {back_to}.", "2"))
    answer = ask("Embedding model?", example)
    parsed = _parsed_or_reprompt(answer, ask, "Embedding model?")
    return None if parsed is None else str(parsed)


async def _stage_ocr(
    *,
    current_ocr: str | None,
    chat_model: str | None,
    env: Mapping[str, str],
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
    connect: Callable[[StageEntry], Awaitable[bool]] | None,
) -> _OcrChoice | _Back:
    """Stage 3: curated and one-keypress-skippable (unset = the built-in ladders)."""
    from smartpipe.cli.screens import tint
    from smartpipe.config.picker import key_stage_entry, ocr_stage_rows

    say(
        tint(
            "  changes document parsing at ingestion - PDFs/images read through the "
            "model (configuring it is the consent; every use is disclosed)",
            "2",
        )
    )
    while True:
        rows = ocr_stage_rows(current_ocr, chat_model)
        back_at = len(rows)
        labels = (*(label for _action, label in rows), "back - embedding model")
        picked = choose("Document OCR - optional:", labels, 0)
        if picked is None or picked == back_at:
            return _BACK
        match rows[picked][0]:
            case "keep":
                return _OcrChoice(current_ocr, changed=False)
            case "unset":
                return _OcrChoice(None, changed=True)
            case "vision":
                return _OcrChoice(chat_model, changed=True)
            case "mistral":
                entry = key_stage_entry(env, "mistral")
                if not entry.connected and (connect is None or not await connect(entry)):
                    if connect is None:
                        return _OcrChoice(current_ocr, changed=False)
                    continue
                return _OcrChoice("mistral/mistral-ocr-latest", changed=True)
            case _:  # "typed"
                say(tint("  Type 'back' to return to the OCR choices.", "2"))
                answer = ask("OCR model?", "mistral-ocr-latest")
                parsed = _parsed_or_reprompt(answer, ask, "OCR model?")
                if parsed is None:
                    continue
                return _OcrChoice(str(parsed), changed=True)


async def _pick_model(
    provider: str,
    *,
    question: str,
    tags: tuple[str, ...] | None,
    fetch_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    chips: ChipSources,
    now: float,
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
    preselect: str | None = None,
) -> str | None:
    """One provider's chat-model menu: live catalog (or local tags) → menu; any
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
        return _typed_model(
            provider,
            question=question,
            ask=ask,
            say=say,
            note=note,
            back_to="provider list",
        )
    shown, hidden = capped_catalog(names)
    type_it = _TYPE_IT if not hidden else f"{_TYPE_IT} ({hidden} more not shown)"
    type_at = len(shown)
    labels = (*model_labels(provider, shown, chips, now), type_it, "back - provider list")
    start = preferred_index(shown) if provider == "ollama" else 0
    preselected = _preselect_index(provider, shown, preselect, default=start)
    while True:
        picked = choose(f"Pick a model ({provider}):", labels, preselected)
        if picked is None or picked == len(labels) - 1:
            return None
        if picked == type_at:
            typed = _typed_model(
                provider,
                question=question,
                ask=ask,
                say=say,
                note=None,
                back_to="model list",
            )
            if typed is None:
                continue
            return typed
        return str(parse_model_ref(f"{provider}/{shown[picked]}"))


def _preselect_index(
    provider: str, shown: tuple[str, ...], preselect: str | None, default: int = 0
) -> int:
    """Idempotence: a re-run starts the cursor on the currently configured model."""
    if preselect is None:
        return default
    return next((i for i, name in enumerate(shown) if f"{provider}/{name}" == preselect), default)


def _typed_model(
    provider: str,
    *,
    question: str,
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
    note: str | None,
    back_to: str,
) -> str | None:
    """The typed-input path, with the provider-prefixed example the wizard shows."""
    from smartpipe.cli.screens import good, tint

    if note is not None:
        say(tint(f"  {note}", "2"))
    example, aside = _EXAMPLES[provider]
    say(tint("  Model names are provider/name:", "2"))
    say("    " + good(example) + tint(f"  ({aside})", "2"))
    say(tint("  Tip: pick one that can SEE images — smartpipe is multimodal;", "2"))
    say(tint("  text-only models refuse image rows.", "2"))
    say(tint(f"  Type 'back' to return to the {back_to}.", "2"))
    answer = ask(question, example)
    parsed = _parsed_or_reprompt(answer, ask, question)
    return None if parsed is None else str(parsed)


async def _pick_fallback(
    env: Mapping[str, str],
    *,
    tags: tuple[str, ...] | None,
    login: Callable[[], bool],
    fetch_catalog: Callable[[str], Awaitable[tuple[str, ...] | None]],
    chips: ChipSources,
    now: float,
    choose: Callable[[str, tuple[str, ...], int], int | None],
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
) -> str | None:
    """One more loop of the picker for the breaker-failover model (item 11) —
    connected providers only; a backup that needs connecting isn't a backup."""
    from smartpipe.config.picker import stage_labels, text_stage_entries

    entries = tuple(
        entry
        for entry in text_stage_entries(env, ollama_up=tags is not None, login=login())
        if entry.connected
    )
    if not entries:
        return None
    while True:
        index = choose("Pick a backup provider:", stage_labels(entries), 0)
        if index is None:
            return None
        fallback = await _pick_model(
            entries[index].provider,
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
            continue
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


def _parsed_or_reprompt(
    answer: str, ask: Callable[[str, str], str], question: str
) -> ModelRef | None:
    """Validate a wizard answer NOW — a saved typo ('\\', stray paste) otherwise
    surfaces later as a confusing provider 400 (owner-hit). Two strikes, then
    UsageFault with the config-command escape hatch."""
    from smartpipe.core.errors import UsageFault

    for attempt in range(2):
        if answer.strip().lower() in _BACK_ANSWERS:
            return None
        try:
            return parse_model_ref(answer.strip())
        except UsageFault as fault:
            if attempt == 1:
                raise
            first = str(fault).splitlines()[0]
            answer = ask(f"{question} (that wasn't a model ref: {first})", "")
    raise AssertionError("unreachable")  # pragma: no cover
