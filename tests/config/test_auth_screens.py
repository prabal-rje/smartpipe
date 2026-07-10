"""Golden pins for the credential door's screens (auth login / list / logout).

Each scenario drives the flow through the NUMBERED fallback with injected
prompts and pins the full transcript - key material never appears in any of
them (the fixtures assert it, and the goldens prove it by inspection).
"""

from __future__ import annotations

from typing import TYPE_CHECKING

from smartpipe.config.authflow import (
    auth_entry,
    connect_api_key,
    list_rows,
    login_menu_labels,
)
from smartpipe.io.arrow_menu import numbered_choose
from smartpipe.models.keycheck import KeyRejected, KeyValid
from tests.helpers.golden import assert_golden

if TYPE_CHECKING:
    from smartpipe.config.authflow import AuthEntry
    from smartpipe.models.keycheck import KeyVerdict

_STORE = "~/.local/share/smartpipe/auth.json"


def _mistral() -> AuthEntry:
    entry = auth_entry("mistral")
    assert entry is not None
    return entry


def test_auth_login_menu_screen_matches_golden() -> None:
    said: list[str] = []
    labels = login_menu_labels(
        env={"OPENAI_API_KEY": "sk-x"},
        stored={"mistral": "mk-stored-000"},
        logged_in=False,
    )
    numbered_choose(
        "Log in to a provider:", labels, 0, ask=lambda _q, default: default, say=said.append
    )
    assert_golden("auth_login_menu", "\n".join(said) + "\n")


async def test_auth_login_key_flow_screen_matches_golden() -> None:
    said: list[str] = []

    async def check(_key: str) -> KeyVerdict:
        return KeyValid()

    await connect_api_key(
        _mistral(),
        secret=lambda _q: "mk-secret-000000",
        choose=lambda _t, _l, _s: 0,
        say=said.append,
        check=check,
        store=lambda _key: None,
        store_display=_STORE,
    )
    rendered = "\n".join(said) + "\n"
    assert "mk-secret-000000" not in rendered  # the pin itself proves no key leaks
    assert_golden("auth_login_key_flow", rendered)


async def test_auth_login_key_rejected_screen_matches_golden() -> None:
    said: list[str] = []
    verdicts: list[KeyVerdict] = [KeyRejected("HTTP 401")]

    async def check(_key: str) -> KeyVerdict:
        return verdicts.pop(0)

    def choose(title: str, labels: tuple[str, ...], start: int) -> int | None:
        return numbered_choose(
            title, labels, start, ask=lambda _q, _d: "3", say=said.append
        )  # skip

    await connect_api_key(
        _mistral(),
        secret=lambda _q: "mk-wrong-000000",
        choose=choose,
        say=said.append,
        check=check,
        store=lambda _key: None,
        store_display=_STORE,
    )
    rendered = "\n".join(said) + "\n"
    assert "mk-wrong-000000" not in rendered
    assert_golden("auth_login_key_rejected", rendered)


def test_auth_list_screen_matches_golden() -> None:
    rows = list_rows(
        env={"OPENAI_API_KEY": "sk-envenvenv9f2"},
        stored={"openai": "sk-storedstored111", "mistral": "mk-storedstored3aa"},
        oauth_account="acct-1",
    )
    rendered = "\n".join(rows) + "\n"
    assert "storedstored" not in rendered and "envenvenv" not in rendered.replace("...9f2", "")
    assert_golden("auth_list", rendered)
