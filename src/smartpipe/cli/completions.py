"""Shell-completion callbacks — instant model-name suggestions, never a hang.

Completion runs on every ``<TAB>``, so the budget is hard: the configured model
comes from the config file, the installed ones from a single Ollama probe capped
at 150 ms — and *any* failure (no daemon, slow daemon, broken config) collapses
to "no suggestions", never an error and never a wait. httpx stays a
function-local import: this module loads with the CLI, the probe runs only
inside a completion request.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

    import click
    from click.shell_completion import CompletionItem

__all__ = ["complete_chat_models", "complete_embed_models", "suggest_models"]

_PROBE_TIMEOUT_SECONDS = 0.15  # a <TAB> must feel instant; a slow probe is a missing probe


def suggest_models(incomplete: str, env: Mapping[str, str], *, embed: bool) -> tuple[str, ...]:
    """Configured model first, then ``ollama/<name>`` per installed model.

    Every failure path degrades to fewer suggestions — completion must never
    crash, hang, or print (the shell would splice the noise into the command line).
    """
    try:
        configured = _configured(env, embed=embed)
    except Exception:  # a broken config file must not break <TAB>
        configured = ()
    try:
        local = tuple(f"ollama/{name}" for name in _ollama_names(env))
    except Exception:  # no daemon / slow daemon / bad payload — suggest nothing extra
        local = ()
    merged = dict.fromkeys(configured + local)  # dedupe, configured first
    return tuple(name for name in merged if name.startswith(incomplete))


def _configured(env: Mapping[str, str], *, embed: bool) -> tuple[str, ...]:
    from smartpipe.config.paths import config_path
    from smartpipe.config.store import load_config

    config = load_config(config_path(env))
    env_value = env.get("SMARTPIPE_EMBED_MODEL" if embed else "SMARTPIPE_MODEL", "").strip()
    stored = config.embed_model if embed else config.model
    return tuple(name for name in (env_value, stored) if name)


def _ollama_names(env: Mapping[str, str]) -> tuple[str, ...]:
    import httpx

    from smartpipe.core.jsontools import as_items, as_record
    from smartpipe.models.ollama import resolve_host

    with httpx.Client(timeout=_PROBE_TIMEOUT_SECONDS) as client:
        response = client.get(f"{resolve_host(env)}/api/tags")
        response.raise_for_status()
        payload = as_record(response.json())
    entries = as_items(payload.get("models")) if payload is not None else None
    if entries is None:
        return ()
    records = (as_record(entry) for entry in entries)
    names = (record.get("name") for record in records if record is not None)
    return tuple(name for name in names if isinstance(name, str))


def complete_chat_models(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    del ctx, param  # click's callback shape; the suggestions need neither
    return _items(incomplete, embed=False)


def complete_embed_models(
    ctx: click.Context, param: click.Parameter, incomplete: str
) -> list[CompletionItem]:
    del ctx, param
    return _items(incomplete, embed=True)


def _items(incomplete: str, *, embed: bool) -> list[CompletionItem]:
    import os

    from click.shell_completion import CompletionItem

    return [CompletionItem(name) for name in suggest_models(incomplete, os.environ, embed=embed)]
