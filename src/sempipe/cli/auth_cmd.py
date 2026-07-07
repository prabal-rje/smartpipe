"""``smartpipe auth`` — log in with ChatGPT, check the login, log out (D19)."""

from __future__ import annotations

import asyncio

import click

from sempipe.core.errors import ExitCode

__all__ = ["auth_command"]


@click.group(name="auth")
def auth_command() -> None:
    """Log in with your ChatGPT Plus/Pro account (OpenAI models, no API key)."""


@auth_command.command(name="login")
@click.option("--headless", is_flag=True, help="Device-code flow for machines without a browser.")
def login(headless: bool) -> None:
    """Log in with ChatGPT. Opens your browser; --headless prints a code instead."""
    asyncio.run(_login(headless=headless))


@auth_command.command(name="status")
def status() -> None:
    """Show the login state (the status line is the result — it goes to stdout)."""
    import os

    from sempipe.config.credentials import credentials_path, load_oauth

    credential = load_oauth(credentials_path(os.environ), "openai")
    if credential is None:
        click.echo("openai: not logged in — run: smartpipe auth login")
        raise SystemExit(int(ExitCode.OK))
    account = credential.account_id or "unknown account"
    click.echo(
        f"openai: logged in with ChatGPT (account {account}) — token refreshes automatically"
    )


@auth_command.command(name="logout")
def logout() -> None:
    """Remove the stored ChatGPT login."""
    import os

    from sempipe.config.credentials import credentials_path, remove_oauth
    from sempipe.io import diagnostics

    if remove_oauth(credentials_path(os.environ), "openai"):
        diagnostics.note("logged out — the stored ChatGPT tokens were removed")
    else:
        diagnostics.note("nothing to remove — you weren't logged in")


async def _login(*, headless: bool) -> None:
    import os

    from sempipe.config.credentials import credentials_path, save_oauth
    from sempipe.io import diagnostics
    from sempipe.models.http_support import make_client
    from sempipe.models.openai_oauth import login_via_browser, poll_device, start_device_flow

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
