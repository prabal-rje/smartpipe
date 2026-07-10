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

    from smartpipe.config.authflow import AuthEntry
    from smartpipe.models.keycheck import KeyVerdict

FRESH_MS = int(time.time() * 1000) + 3_600_000


@pytest.fixture
def home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
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
    code, _out, err = run_cli(["auth", "login", "openai"])  # openai = ChatGPT (back-compat)
    assert code == 0
    assert "logged in" in err


# --- the key path: connect flow (injected I/O; the transcript never shows a key) -----


class _Session:
    """A scripted connect session: prompts to feed, everything said, recorded stores."""

    def __init__(self, secrets: list[str], picks: list[int | None] | None = None) -> None:
        self.secrets = secrets
        self.picks = picks or []
        self.said: list[str] = []
        self.stored: list[str] = []

    def secret(self, _question: str) -> str:
        return self.secrets.pop(0)

    def choose(self, _title: str, _labels: tuple[str, ...], _start: int) -> int | None:
        return self.picks.pop(0)

    def store(self, key: str) -> None:
        self.stored.append(key)


def _entry(entry_id: str) -> AuthEntry:
    from smartpipe.config.authflow import auth_entry

    entry = auth_entry(entry_id)
    assert entry is not None
    return entry


async def _connect(session: _Session, entry_id: str, verdicts: list[KeyVerdict]) -> bool:
    from smartpipe.config.authflow import connect_api_key

    async def check(_key: str) -> KeyVerdict:
        return verdicts.pop(0)

    return await connect_api_key(
        _entry(entry_id),
        secret=session.secret,
        choose=session.choose,
        say=session.said.append,
        check=check,
        store=session.store,
        store_display="~/.local/share/smartpipe/auth.json",
    )


async def test_connect_stores_a_valid_key_and_never_echoes_it() -> None:
    from smartpipe.models.keycheck import KeyValid

    session = _Session(secrets=["mk-secret-value-123"])
    assert await _connect(session, "mistral", [KeyValid()]) is True
    assert session.stored == ["mk-secret-value-123"]
    transcript = "\n".join(session.said)
    assert "mk-secret-value-123" not in transcript  # never print key material
    assert "https://console.mistral.ai/api-keys" in transcript
    assert "auth logout mistral" in transcript
    assert "owner-only" in transcript


async def test_connect_rejected_then_retry_succeeds() -> None:
    from smartpipe.models.keycheck import KeyRejected, KeyValid

    session = _Session(secrets=["bad-key-000000", "good-key-11111"], picks=[0])
    assert await _connect(session, "mistral", [KeyRejected("HTTP 401"), KeyValid()]) is True
    assert session.stored == ["good-key-11111"]
    assert any("HTTP 401" in line for line in session.said)


async def test_connect_rejected_store_anyway() -> None:
    from smartpipe.models.keycheck import KeyRejected

    session = _Session(secrets=["maybe-key-0000"], picks=[1])
    assert await _connect(session, "mistral", [KeyRejected("couldn't reach api.mistral.ai")])
    assert session.stored == ["maybe-key-0000"]  # the provider may be having a bad minute


async def test_connect_rejected_skip_stores_nothing() -> None:
    from smartpipe.models.keycheck import KeyRejected

    session = _Session(secrets=["bad-key-000000"], picks=[2])
    assert await _connect(session, "mistral", [KeyRejected("HTTP 401")]) is False
    assert session.stored == []


async def test_connect_empty_input_skips() -> None:
    session = _Session(secrets=["   "])
    assert await _connect(session, "anthropic", []) is False
    assert session.stored == []


async def test_connect_unchecked_provider_stores_with_a_note() -> None:
    from smartpipe.models.keycheck import KeyUnchecked

    session = _Session(secrets=["jina-key-000000"])
    assert await _connect(session, "jina", [KeyUnchecked("jina has no free check endpoint")])
    assert session.stored == ["jina-key-000000"]
    assert any("unchecked" in line for line in session.said)


# --- the login menu + dispatch ---------------------------------------------------------


def test_login_menu_lists_openai_twice_with_badges() -> None:
    from smartpipe.config.authflow import login_menu_labels

    labels = login_menu_labels(
        env={"OPENAI_API_KEY": "sk-x"}, stored={"mistral": "mk"}, logged_in=True
    )
    assert len(labels) == 7
    assert labels[0].startswith("openai (API key)")
    assert "✓ key (env)" in labels[0]
    assert labels[1].startswith("openai (ChatGPT login)")
    assert "✓ ChatGPT" in labels[1]
    assert any(line.startswith("mistral") and "✓ key (stored)" in line for line in labels)
    assert any(line.startswith("anthropic") and "needs key" in line for line in labels)
    assert any(line.startswith("jina") and "embeddings only" in line for line in labels)


async def test_login_dispatch_menu_routes_to_the_key_path(
    respx_mock: respx.MockRouter, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from smartpipe.cli.auth_cmd import login_dispatch
    from smartpipe.config.credentials import keys_path, load_api_key

    respx_mock.get("https://api.mistral.ai/v1/models").mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    picked: list[str] = []

    def choose(title: str, labels: tuple[str, ...], _start: int) -> int:
        picked.extend(labels)
        return next(i for i, label in enumerate(labels) if label.startswith("mistral"))

    await login_dispatch(None, headless=False, secret=lambda _q: "mk-live-key-000", choose=choose)
    assert load_api_key(keys_path(os.environ), "mistral") == "mk-live-key-000"
    assert any(label.startswith("openai (ChatGPT login)") for label in picked)


async def test_login_dispatch_named_provider_skips_the_menu(
    respx_mock: respx.MockRouter,
) -> None:
    import os

    from smartpipe.cli.auth_cmd import login_dispatch
    from smartpipe.config.credentials import keys_path, load_api_key

    respx_mock.get("https://api.anthropic.com/v1/models", params={"limit": "1"}).mock(
        return_value=httpx.Response(200, json={"data": []})
    )
    await login_dispatch(
        "anthropic",
        headless=False,
        secret=lambda _q: "sk-ant-stored-0",
        choose=lambda _t, _l, _s: pytest.fail("no menu expected"),
    )
    assert load_api_key(keys_path(os.environ), "anthropic") == "sk-ant-stored-0"


def test_login_unknown_provider_is_a_usage_fault(run_cli: RunCli, home: Path) -> None:
    code, _out, err = run_cli(["auth", "login", "acme"])
    assert code == 64
    assert "unknown provider" in err and "openai-api" in err


# --- auth list --------------------------------------------------------------------------


def test_auth_list_empty(run_cli: RunCli, home: Path) -> None:
    code, out, _err = run_cli(["auth", "list"])
    assert code == 0
    assert out == "no credentials - connect one: smartpipe auth login\n"


def test_auth_list_masks_and_names_the_live_source(
    run_cli: RunCli, home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import os

    from smartpipe.config.credentials import keys_path, save_api_key

    _login(home)  # the ChatGPT login row
    monkeypatch.setenv("OPENAI_API_KEY", "sk-envenvenv9f2")
    save_api_key(keys_path(os.environ), "openai", "sk-storedstored111")
    save_api_key(keys_path(os.environ), "mistral", "mk-storedstored3aa")
    code, out, _err = run_cli(["auth", "list"])
    assert code == 0
    assert "sk-...9f2" in out  # masked env key
    assert "env OPENAI_API_KEY (stored key shadowed)" in out  # env always wins
    assert "account acct-1" in out
    assert "mk-...3aa" in out and "stored" in out
    assert "sk-envenvenv9f2" not in out and "mk-storedstored3aa" not in out  # never the key


# --- auth logout ------------------------------------------------------------------------


def test_auth_logout_named_key(run_cli: RunCli, home: Path) -> None:
    import os

    from smartpipe.config.credentials import keys_path, load_api_key, save_api_key

    save_api_key(keys_path(os.environ), "mistral", "mk-1-000000")
    code, _out, err = run_cli(["auth", "logout", "mistral"])
    assert code == 0
    assert "removed the stored mistral API key" in err
    assert load_api_key(keys_path(os.environ), "mistral") is None


def test_auth_logout_openai_still_means_the_login(run_cli: RunCli, home: Path) -> None:
    import os

    from smartpipe.config.credentials import keys_path, load_api_key, save_api_key

    _login(home)
    save_api_key(keys_path(os.environ), "openai", "sk-key-0000000")
    code, _out, err = run_cli(["auth", "logout", "openai"])
    assert code == 0
    assert "ChatGPT tokens were removed" in err
    assert load_api_key(keys_path(os.environ), "openai") == "sk-key-0000000"  # untouched
    code, _out, err = run_cli(["auth", "logout", "openai-api"])
    assert code == 0
    assert "removed the stored openai API key" in err
    assert load_api_key(keys_path(os.environ), "openai") is None


def test_auth_logout_no_arg_single_candidate_removes_it(run_cli: RunCli, home: Path) -> None:
    _login(home)
    code, _out, err = run_cli(["auth", "logout"])
    assert code == 0
    assert "ChatGPT tokens were removed" in err  # the old bare-logout behavior survives


def test_logout_candidates_cover_both_stores() -> None:
    from smartpipe.config.authflow import logout_candidates

    entries = logout_candidates(stored={"mistral": "mk", "openai": "sk"}, logged_in=True)
    ids = tuple(entry.entry_id for entry in entries)
    assert ids == ("openai-api", "openai", "mistral")
