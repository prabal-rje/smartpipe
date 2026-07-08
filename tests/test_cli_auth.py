"""smartpipe auth + the D19 precedence: key > login > the dual-fix screen."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.config.credentials import OAuthCredential, save_oauth
from smartpipe.models.openai_codex import CODEX_ENDPOINT
from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path

    import respx

FRESH_MS = int(time.time() * 1000) + 3_600_000


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path)
    monkeypatch.setenv("APPDATA", str(tmp_path)  # the windows config root (D09))  # the store lands under tmp
    monkeypatch.delenv("OPENAI_API_KEY", raising=False)
    monkeypatch.setenv("SMARTPIPE_MODEL", "openai/gpt-5.4")
    return tmp_path / "smartpipe" / "auth.json"


def _login(path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    save_oauth(path, "openai", OAuthCredential("at-1", "rt-1", FRESH_MS, "acct-1"))


def test_status_logged_out(run_cli: RunCli, home: Path) -> None:
    code, out, _err = run_cli(["auth", "status"])
    assert code == 0
    assert out == "openai: not logged in — run: smartpipe auth login\n"


def test_status_logged_in(run_cli: RunCli, home: Path) -> None:
    _login(home)
    code, out, _err = run_cli(["auth", "status"])
    assert code == 0
    assert (
        out == "openai: logged in with ChatGPT (account acct-1) — token refreshes automatically\n"
    )


def test_logout_removes_the_tokens(run_cli: RunCli, home: Path) -> None:
    _login(home)
    code, _out, err = run_cli(["auth", "logout"])
    assert code == 0
    assert "removed" in err
    assert json.loads(home.read_text()) == {}


# --- precedence (D19) ---------------------------------------------------------------


SSE = (
    'data: {"type": "response.output_text.delta", "delta": "ok"}\n\n'
    'data: {"type": "response.completed", "response": {"output": '
    '[{"type": "message", "content": [{"type": "output_text", "text": "ok"}]}]}}'
    "\n\ndata: [DONE]\n\n"
)


def test_api_key_takes_precedence_over_login(
    run_cli: RunCli, home: Path, monkeypatch: pytest.MonkeyPatch, respx_mock: respx.MockRouter
) -> None:
    _login(home)
    monkeypatch.setenv("OPENAI_API_KEY", "sk-explicit")
    platform = respx_mock.post("https://api.openai.com/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "ok"}}]})
    )
    code, out, _err = run_cli(["map", "echo"], stdin="hi\n")
    assert code == 0 and out == "ok\n"
    assert platform.call_count == 1  # an explicit, billable key is deliberate — it wins


def test_login_rides_the_codex_wire(
    run_cli: RunCli, home: Path, respx_mock: respx.MockRouter
) -> None:
    _login(home)
    codex = respx_mock.post(CODEX_ENDPOINT).mock(return_value=httpx.Response(200, text=SSE))
    code, out, _err = run_cli(["map", "echo"], stdin="hi\n")
    assert code == 0 and out == "ok\n"
    from tests.helpers.wire import sent_header

    assert sent_header(codex, "chatgpt-account-id") == "acct-1"


def test_neither_shows_the_dual_fix_screen(run_cli: RunCli, home: Path) -> None:
    code, _out, err = run_cli(["map", "echo"], stdin="hi\n")
    assert code == 2
    assert "OPENAI_API_KEY" in err and "smartpipe auth login" in err  # both fixes offered


def test_embeddings_on_login_only_point_at_keys(
    run_cli: RunCli, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _login(home)
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "openai/text-embedding-3-small")
    code, _out, err = run_cli(["embed"], stdin="hi\n")
    assert code == 2
    assert "embeddings aren't available through ChatGPT login" in err


# --- the login flows through the CLI (issuer mocked, browser injected) ------------


def test_login_headless_flow(run_cli: RunCli, home: Path, respx_mock: respx.MockRouter) -> None:
    from smartpipe.models.openai_oauth import ISSUER

    respx_mock.post(f"{ISSUER}/api/accounts/deviceauth/usercode").mock(
        return_value=httpx.Response(
            200, json={"device_auth_id": "d", "user_code": "WXYZ-9999", "interval": "0.01"}
        )
    )
    respx_mock.post(f"{ISSUER}/api/accounts/deviceauth/token").mock(
        return_value=httpx.Response(200, json={"authorization_code": "AC", "code_verifier": "CV"})
    )
    respx_mock.post(f"{ISSUER}/oauth/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "at-new", "refresh_token": "rt-new", "expires_in": 3600}
        )
    )
    code, _out, err = run_cli(["auth", "login", "--headless"])
    assert code == 0
    assert "WXYZ-9999" in err  # the code the user types
    assert "logged in" in err
    from smartpipe.config.credentials import load_oauth

    stored = load_oauth(home, "openai")
    assert stored is not None and stored.access == "at-new"


def test_login_browser_flow(
    run_cli: RunCli, home: Path, respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import asyncio
    from urllib.parse import parse_qs, urlparse

    from smartpipe.models.openai_oauth import ISSUER

    respx_mock.post(f"{ISSUER}/oauth/token").mock(
        return_value=httpx.Response(
            200, json={"access_token": "at-b", "refresh_token": "rt-b", "expires_in": 3600}
        )
    )
    respx_mock.route(host="localhost").pass_through()

    def fake_open(url: str) -> bool:
        state = parse_qs(urlparse(url).query)["state"][0]

        async def visit() -> None:
            await asyncio.sleep(0.05)
            async with httpx.AsyncClient() as browser:
                await browser.get(
                    "http://localhost:1455/auth/callback",
                    params={"code": "CODE", "state": state},
                )

        asyncio.get_event_loop().create_task(visit())
        return True

    monkeypatch.setattr("webbrowser.open", fake_open)
    code, _out, err = run_cli(["auth", "login"])
    assert code == 0
    assert "logged in" in err
