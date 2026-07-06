"""The ChatGPT login engine: pure pieces unit-tested, every issuer wire respx-pinned,
and the browser flow driven end-to-end with a pass-through localhost callback."""

from __future__ import annotations

import asyncio
import base64
import hashlib
import json
from typing import TYPE_CHECKING
from urllib.parse import parse_qs, urlparse

import httpx
import pytest
import respx

from sempipe.core.errors import SetupFault
from sempipe.models.http_support import make_client
from sempipe.models.openai_oauth import (
    CLIENT_ID,
    ISSUER,
    DeviceStart,
    authorize_url,
    credential_from_tokens,
    exchange_code,
    extract_account_id,
    generate_pkce,
    login_via_browser,
    poll_device,
    refresh_tokens,
    start_device_flow,
)
from tests.helpers.wire import sent_form

if TYPE_CHECKING:
    from collections.abc import AsyncIterator


def _jwt(claims: dict[str, object]) -> str:
    body = base64.urlsafe_b64encode(json.dumps(claims).encode()).decode().rstrip("=")
    return f"h.{body}.sig"


TOKENS = {
    "access_token": "at-1",
    "refresh_token": "rt-1",
    "id_token": _jwt({"chatgpt_account_id": "acct-1"}),
    "expires_in": 3600,
}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c


# --- pure pieces ---------------------------------------------------------------


def test_pkce_is_s256_of_the_verifier() -> None:
    pkce = generate_pkce()
    assert len(pkce.verifier) == 43
    digest = hashlib.sha256(pkce.verifier.encode()).digest()
    expected = base64.urlsafe_b64encode(digest).decode().rstrip("=")
    assert pkce.challenge == expected


def test_authorize_url_pins_the_protocol() -> None:
    pkce = generate_pkce()
    url = authorize_url("http://localhost:1455/auth/callback", pkce, "STATE")
    parsed = urlparse(url)
    assert f"{parsed.scheme}://{parsed.netloc}{parsed.path}" == f"{ISSUER}/oauth/authorize"
    query = {k: v[0] for k, v in parse_qs(parsed.query).items()}
    assert query["client_id"] == CLIENT_ID
    assert query["code_challenge"] == pkce.challenge
    assert query["code_challenge_method"] == "S256"
    assert query["scope"] == "openid profile email offline_access"
    assert query["codex_cli_simplified_flow"] == "true"
    assert query["originator"] == "sempipe"  # honest self-identification, D19
    assert query["state"] == "STATE"


def test_account_id_from_all_three_claim_locations() -> None:
    direct = _jwt({"chatgpt_account_id": "a"})
    nested = _jwt({"https://api.openai.com/auth": {"chatgpt_account_id": "b"}})
    org = _jwt({"organizations": [{"id": "c"}]})
    assert extract_account_id(direct, None) == "a"
    assert extract_account_id(nested, None) == "b"
    assert extract_account_id(org, None) == "c"
    assert extract_account_id(None, direct) == "a"  # access-token fallback
    assert extract_account_id("garbage", "also.garbage") is None


def test_credential_from_tokens_math_and_faults() -> None:
    credential = credential_from_tokens(TOKENS)
    assert credential.access == "at-1" and credential.refresh == "rt-1"
    assert credential.account_id == "acct-1"
    with pytest.raises(SetupFault, match="missing tokens"):
        credential_from_tokens({"access_token": "only"})


# --- issuer wires ----------------------------------------------------------------


async def test_exchange_sends_the_pinned_form(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(f"{ISSUER}/oauth/token").mock(
        return_value=httpx.Response(200, json=TOKENS)
    )
    credential = await exchange_code(client, "CODE", "http://localhost:1455/auth/callback", "VER")
    assert credential.access == "at-1"
    form = sent_form(route)
    assert form == {
        "grant_type": "authorization_code",
        "code": "CODE",
        "redirect_uri": "http://localhost:1455/auth/callback",
        "client_id": CLIENT_ID,
        "code_verifier": "VER",
    }


async def test_refresh_sends_the_pinned_form(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(f"{ISSUER}/oauth/token").mock(
        return_value=httpx.Response(200, json=TOKENS)
    )
    await refresh_tokens(client, "rt-0")
    form = sent_form(route)
    assert form == {"grant_type": "refresh_token", "refresh_token": "rt-0", "client_id": CLIENT_ID}


async def test_device_flow_polls_until_ready(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{ISSUER}/api/accounts/deviceauth/usercode").mock(
        return_value=httpx.Response(
            200, json={"device_auth_id": "dev-1", "user_code": "ABCD-1234", "interval": "1"}
        )
    )
    respx_mock.post(f"{ISSUER}/api/accounts/deviceauth/token").side_effect = [
        httpx.Response(403),  # pending
        httpx.Response(404),  # still pending (the other pending status)
        httpx.Response(200, json={"authorization_code": "AC", "code_verifier": "CV"}),
    ]
    respx_mock.post(f"{ISSUER}/oauth/token").mock(return_value=httpx.Response(200, json=TOKENS))
    start = await start_device_flow(client)
    assert start.user_code == "ABCD-1234"
    sleeps: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        sleeps.append(seconds)

    credential = await poll_device(client, start, sleep=fake_sleep)
    assert credential.access == "at-1"
    assert len(sleeps) == 2  # slept once per pending response, never busy-looped


async def test_device_flow_unexpected_status_fails_loudly(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{ISSUER}/api/accounts/deviceauth/token").mock(
        return_value=httpx.Response(500)
    )
    start = DeviceStart(device_auth_id="d", user_code="u", interval_s=1, verify_url="x")
    with pytest.raises(SetupFault, match="device login failed"):
        await poll_device(client, start)


# --- the browser flow, end to end --------------------------------------------------


async def test_browser_flow_end_to_end(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{ISSUER}/oauth/token").mock(return_value=httpx.Response(200, json=TOKENS))
    respx_mock.route(host="127.0.0.1").pass_through()  # the callback server is real
    port = 14550  # off the real port so a parallel test run can't collide

    def fake_browser(url: str) -> None:
        query = {k: v[0] for k, v in parse_qs(urlparse(url).query).items()}

        async def visit() -> None:  # what the user's browser does after consent
            async with httpx.AsyncClient() as browser_client:
                await browser_client.get(
                    # the v4 loopback, matching the server bind: "localhost" order
                    # varies by host and a refused ::1 connect would hang the flow
                    f"http://127.0.0.1:{port}/auth/callback",
                    params={"code": "CODE", "state": query["state"]},
                )

        asyncio.get_running_loop().create_task(visit())

    credential = await asyncio.wait_for(
        login_via_browser(client, open_browser=fake_browser, port=port), timeout=10
    )
    assert credential.access == "at-1"
    assert credential.account_id == "acct-1"


async def test_browser_flow_rejects_a_wrong_state(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.route(host="localhost").pass_through()
    port = 14551

    def fake_browser(_url: str) -> None:
        async def visit() -> None:
            async with httpx.AsyncClient() as browser_client:
                await browser_client.get(
                    f"http://localhost:{port}/auth/callback",
                    params={"code": "CODE", "state": "FORGED"},
                )

        asyncio.get_running_loop().create_task(visit())

    with pytest.raises(SetupFault, match="invalid OAuth state"):
        await asyncio.wait_for(
            login_via_browser(client, open_browser=fake_browser, port=port), timeout=10
        )


async def test_port_in_use_suggests_headless(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    import socket

    respx_mock.route(host="localhost").pass_through()
    blocker = socket.socket()
    blocker.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    blocker.bind(("localhost", 14552))
    blocker.listen(1)
    try:

        def noop(_url: str) -> None:
            return None

        with pytest.raises(SetupFault, match="--headless"):
            await login_via_browser(client, open_browser=noop, port=14552)
    finally:
        blocker.close()


async def test_device_start_malformed_reply(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{ISSUER}/api/accounts/deviceauth/usercode").mock(
        return_value=httpx.Response(200, json={"user_code": "only"})  # no device_auth_id
    )
    with pytest.raises(SetupFault, match="malformed"):
        await start_device_flow(client)


async def test_device_start_http_error(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(f"{ISSUER}/api/accounts/deviceauth/usercode").mock(
        return_value=httpx.Response(503)
    )
    with pytest.raises(SetupFault, match="couldn't start"):
        await start_device_flow(client)


async def test_browser_flow_reports_an_error_param(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.route(host="localhost").pass_through()
    port = 14553

    def fake_browser(_url: str) -> None:
        async def visit() -> None:
            async with httpx.AsyncClient() as browser:
                await browser.get(
                    f"http://localhost:{port}/auth/callback",
                    params={"error_description": "access denied"},
                )

        asyncio.get_running_loop().create_task(visit())

    with pytest.raises(SetupFault, match="access denied"):
        await asyncio.wait_for(
            login_via_browser(client, open_browser=fake_browser, port=port), timeout=10
        )
