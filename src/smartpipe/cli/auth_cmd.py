"""``smartpipe auth`` - the credential door for every provider (D19 + auth wave).

``login`` with no argument opens a menu of ALL providers; OpenAI appears twice
because its two wires differ (API key vs ChatGPT login - the login wire has no
embeddings). The ChatGPT entry routes to the original OAuth flow unchanged;
every other entry takes the key path: create-a-key URL, masked prompt, one
live catalog GET, then the 0600 key store. ``auth login openai`` keeps meaning
the ChatGPT OAuth flow (back-compat); the key wire is ``auth login openai-api``.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

import click

from smartpipe.core.errors import ExitCode, UsageFault

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

    from smartpipe.config.authflow import AuthEntry

__all__ = ["auth_command", "login_dispatch"]

_ENTRY_IDS = "openai-api, openai (ChatGPT), anthropic, gemini, mistral, openrouter, jina"


@click.group(name="auth")
def auth_command() -> None:
    """Connect providers: store API keys, or log in with ChatGPT."""


@auth_command.command(name="login")
@click.argument("provider", required=False)
@click.option(
    "--headless", is_flag=True, help="ChatGPT device-code flow for machines without a browser."
)
def login(provider: str | None, headless: bool) -> None:
    """Connect a provider (menu when PROVIDER is omitted).

    \b
      smartpipe auth login              pick a provider from the list
      smartpipe auth login mistral      store a Mistral API key
      smartpipe auth login openai       log in with ChatGPT (back-compat)
      smartpipe auth login openai-api   store an OpenAI API key
    """
    asyncio.run(login_dispatch(provider, headless=headless))


@auth_command.command(name="status")
def status() -> None:
    """Show the login state (the status line is the result - it goes to stdout)."""
    import os

    from smartpipe.config.credentials import credentials_path, load_oauth

    credential = load_oauth(credentials_path(os.environ), "openai")
    if credential is None:
        click.echo("openai: not logged in — run: smartpipe auth login")
        raise SystemExit(int(ExitCode.OK))
    account = credential.account_id or "unknown account"
    click.echo(
        f"openai: logged in with ChatGPT (account {account}) — token refreshes automatically"
    )


@auth_command.command(name="list")
def list_credentials() -> None:
    """List connected providers: type, masked key, and which source is live."""
    import os

    from smartpipe.config.authflow import list_rows
    from smartpipe.config.credentials import (
        credentials_path,
        keys_path,
        load_oauth,
        stored_api_keys,
    )

    credential = load_oauth(credentials_path(os.environ), "openai")
    rows = list_rows(
        env=os.environ,
        stored=stored_api_keys(keys_path(os.environ)),
        oauth_account=(credential.account_id or "unknown") if credential else None,
    )
    if not rows:
        click.echo("no credentials - connect one: smartpipe auth login")
        return
    for row in rows:
        click.echo(row)


@auth_command.command(name="logout")
@click.argument("provider", required=False)
def logout(provider: str | None) -> None:
    """Remove a stored credential (picker when PROVIDER is omitted)."""
    import os

    from smartpipe.config.authflow import auth_entry, logout_candidates
    from smartpipe.config.credentials import (
        credentials_path,
        keys_path,
        load_oauth,
        stored_api_keys,
    )
    from smartpipe.io import diagnostics

    keys = keys_path(os.environ)
    logins = credentials_path(os.environ)
    if provider is not None:
        entry = auth_entry(provider)
        if entry is None:
            raise UsageFault(f"unknown provider {provider!r} - one of: {_ENTRY_IDS}")
        _remove(entry, keys=keys, logins=logins)
        return
    candidates = logout_candidates(
        stored=stored_api_keys(keys),
        logged_in=load_oauth(logins, "openai") is not None,
    )
    if not candidates:
        diagnostics.note("nothing to remove — you weren't logged in")
        return
    if len(candidates) == 1:
        _remove(candidates[0], keys=keys, logins=logins)
        return
    labels = tuple(entry.label for entry in candidates)
    picked = _choose("Remove which credential?", labels, 0)
    if picked is None:
        diagnostics.note("nothing removed")
        return
    _remove(candidates[picked], keys=keys, logins=logins)


def _remove(entry: AuthEntry, *, keys: Path, logins: Path) -> None:
    from smartpipe.config.credentials import remove_api_key, remove_oauth
    from smartpipe.io import diagnostics

    if entry.kind == "oauth":
        if remove_oauth(logins, "openai"):
            diagnostics.note("logged out — the stored ChatGPT tokens were removed")
        else:
            diagnostics.note("nothing to remove — you weren't logged in")
        return
    if remove_api_key(keys, entry.provider):
        diagnostics.note(f"removed the stored {entry.provider} API key")
    else:
        diagnostics.note(f"no stored {entry.provider} API key to remove")


async def login_dispatch(
    target: str | None,
    *,
    headless: bool,
    secret: Callable[[str], str] | None = None,
    choose: Callable[[str, tuple[str, ...], int], int | None] | None = None,
) -> None:
    """Route a login: menu → entry → OAuth flow or the key path."""
    import os

    from smartpipe.config.authflow import AUTH_ENTRIES, auth_entry, login_menu_labels
    from smartpipe.config.credentials import (
        credentials_path,
        keys_path,
        load_oauth,
        stored_api_keys,
    )

    pick = choose or _choose
    if target is None and headless:
        entry = next(e for e in AUTH_ENTRIES if e.kind == "oauth")  # back-compat: device flow
    elif target is None:
        labels = login_menu_labels(
            env=os.environ,
            stored=stored_api_keys(keys_path(os.environ)),
            logged_in=load_oauth(credentials_path(os.environ), "openai") is not None,
        )
        picked = pick("Log in to a provider:", labels, 0)
        if picked is None:
            from smartpipe.io import diagnostics

            diagnostics.note("nothing chosen — no credentials changed")
            return
        entry = AUTH_ENTRIES[picked]
    else:
        found = auth_entry(target)
        if found is None:
            raise UsageFault(f"unknown provider {target!r} - one of: {_ENTRY_IDS}")
        entry = found
    if entry.kind == "oauth":
        await _login(headless=headless)
        return
    await _key_login(entry, secret=secret or _secret_prompt)


async def _key_login(entry: AuthEntry, *, secret: Callable[[str], str]) -> None:
    import os
    from functools import partial

    from smartpipe.config.authflow import connect_api_key
    from smartpipe.config.credentials import keys_path, save_api_key
    from smartpipe.config.paths import human_path
    from smartpipe.models.http_support import make_client
    from smartpipe.models.keycheck import check_api_key

    store_path = keys_path(os.environ)
    async with make_client() as client:
        await connect_api_key(
            entry,
            secret=secret,
            choose=_choose,
            say=click.echo,
            check=partial(check_api_key, entry.provider, env=os.environ, client=client),
            store=partial(save_api_key, store_path, entry.provider),
            store_display=human_path(store_path),
        )


def _secret_prompt(question: str) -> str:  # pragma: no cover - real terminal input
    return str(click.prompt(question, hide_input=True, default="", show_default=False))


def _choose(title: str, labels: tuple[str, ...], start: int) -> int | None:
    """Arrow menu on a real terminal, the numbered prompt everywhere else."""
    import os
    import sys

    from smartpipe.io.arrow_menu import arrow_choose, menu_capable, numbered_choose

    if menu_capable(
        stdin_tty=sys.stdin.isatty(),
        stdout_tty=sys.stdout.isatty(),
        term=os.environ.get("TERM"),
    ):  # pragma: no cover - raw terminal I/O
        return arrow_choose(title, labels, sys.stdout, start=start)
    return numbered_choose(
        title,
        labels,
        start,
        ask=lambda question, default: str(click.prompt(question, default=default)),
        say=click.echo,
    )


async def _login(*, headless: bool) -> None:
    import os

    from smartpipe.config.credentials import credentials_path, save_oauth
    from smartpipe.io import diagnostics
    from smartpipe.models.http_support import make_client
    from smartpipe.models.openai_oauth import login_via_browser, poll_device, start_device_flow

    async with make_client() as client:
        if headless:
            start = await start_device_flow(client)
            diagnostics.note(f"open {start.verify_url} and enter code: {start.user_code}")
            credential = await poll_device(client, start)
        else:

            def announce(url: str) -> None:
                diagnostics.note(f"complete the login in your browser (opening): {url}")

            credential = await login_via_browser(client, announce=announce)
    save_oauth(credentials_path(os.environ), "openai", credential)
    account = credential.account_id or "unknown account"
    diagnostics.note(f"logged in with ChatGPT (account {account})")
