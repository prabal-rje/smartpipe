"""OpenAI "Log in with ChatGPT" — the OAuth engine (plan/decisions.md D19).

Protocol transcribed from opencode's working implementation (context/opencode):
OpenAI's public Codex OAuth client, PKCE S256, a localhost:1455 callback for the
browser flow, and a device-code flow for headless machines. sempipe self-identifies
with ``originator: sempipe`` exactly as opencode does with its own name.

The pure pieces (PKCE, URLs, JWT claims) are unit-tested; the HTTP pieces take an
injected ``httpx.AsyncClient`` so respx pins every wire shape.
"""

from __future__ import annotations

import asyncio
import base64
import contextlib
import hashlib
import json
import secrets
import time
from dataclasses import dataclass
from typing import TYPE_CHECKING

from sempipe.config.credentials import OAuthCredential
from sempipe.core.errors import SetupFault
from sempipe.core.jsontools import as_items, as_record, as_str

if TYPE_CHECKING:
    import httpx

__all__ = [
    "CALLBACK_PORT",
    "CLIENT_ID",
    "ISSUER",
    "DeviceStart",
    "Pkce",
    "authorize_url",
    "credential_from_tokens",
    "exchange_code",
    "extract_account_id",
    "generate_pkce",
    "login_via_browser",
    "poll_device",
    "refresh_tokens",
    "start_device_flow",
]

CLIENT_ID = "app_EMoamEEZ73f0CkXaXp7hrann"  # OpenAI's public Codex OAuth client
ISSUER = "https://auth.openai.com"
CALLBACK_PORT = 1455
_SCOPE = "openid profile email offline_access"
_ORIGINATOR = "sempipe"
_LOGIN_TIMEOUT_S = 300.0  # the browser flow's 5-minute cap
_DEVICE_PENDING = (403, 404)  # "keep polling" statuses, per the wire


@dataclass(frozen=True, slots=True)
class Pkce:
    verifier: str
    challenge: str


@dataclass(frozen=True, slots=True)
class DeviceStart:
    device_auth_id: str
    user_code: str
    interval_s: float
    verify_url: str


def generate_pkce() -> Pkce:
    charset = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789-._~"
    verifier = "".join(secrets.choice(charset) for _ in range(43))
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    return Pkce(verifier=verifier, challenge=_b64url(digest))


def authorize_url(redirect_uri: str, pkce: Pkce, state: str) -> str:
    from urllib.parse import urlencode

    params = {
        "response_type": "code",
        "client_id": CLIENT_ID,
        "redirect_uri": redirect_uri,
        "scope": _SCOPE,
        "code_challenge": pkce.challenge,
        "code_challenge_method": "S256",
        "id_token_add_organizations": "true",
        "codex_cli_simplified_flow": "true",
        "state": state,
        "originator": _ORIGINATOR,
    }
    return f"{ISSUER}/oauth/authorize?{urlencode(params)}"


def extract_account_id(id_token: str | None, access_token: str | None) -> str | None:
    for token in (id_token, access_token):
        if token is None:
            continue
        claims = _jwt_claims(token)
        if claims is None:
            continue
        direct = as_str(claims.get("chatgpt_account_id"))
        if direct is not None:
            return direct
        nested = as_record(claims.get("https://api.openai.com/auth"))
        if nested is not None:
            nested_id = as_str(nested.get("chatgpt_account_id"))
            if nested_id is not None:
                return nested_id
        organizations = as_items(claims.get("organizations"))
        if organizations:
            first = as_record(organizations[0])
            if first is not None:
                org = as_str(first.get("id"))
                if org is not None:
                    return org
    return None


def credential_from_tokens(payload: object) -> OAuthCredential:
    record = as_record(payload)
    access = as_str(record.get("access_token")) if record is not None else None
    refresh = as_str(record.get("refresh_token")) if record is not None else None
    if record is None or access is None or refresh is None:
        raise SetupFault("error: the login reply was missing tokens — try again")
    expires_in = record.get("expires_in")
    seconds = expires_in if isinstance(expires_in, int) else 3600
    return OAuthCredential(
        access=access,
        refresh=refresh,
        expires_ms=int(time.time() * 1000) + seconds * 1000,
        account_id=extract_account_id(as_str(record.get("id_token")), access),
    )


async def exchange_code(
    client: httpx.AsyncClient, code: str, redirect_uri: str, verifier: str
) -> OAuthCredential:
    return credential_from_tokens(
        await _token_request(
            client,
            {
                "grant_type": "authorization_code",
                "code": code,
                "redirect_uri": redirect_uri,
                "client_id": CLIENT_ID,
                "code_verifier": verifier,
            },
        )
    )


async def refresh_tokens(client: httpx.AsyncClient, refresh_token: str) -> OAuthCredential:
    return credential_from_tokens(
        await _token_request(
            client,
            {
                "grant_type": "refresh_token",
                "refresh_token": refresh_token,
                "client_id": CLIENT_ID,
            },
        )
    )


async def start_device_flow(client: httpx.AsyncClient) -> DeviceStart:
    response = await client.post(
        f"{ISSUER}/api/accounts/deviceauth/usercode",
        json={"client_id": CLIENT_ID},
        headers={"User-Agent": _user_agent()},
    )
    if response.status_code != 200:
        raise SetupFault(
            f"error: couldn't start the device login (HTTP {response.status_code})\n"
            "  Check your network and try again: sempipe auth login --headless"
        )
    record = as_record(response.json())
    device_id = as_str(record.get("device_auth_id")) if record is not None else None
    user_code = as_str(record.get("user_code")) if record is not None else None
    if record is None or device_id is None or user_code is None:
        raise SetupFault("error: the device login reply was malformed — try again")
    raw_interval = as_str(record.get("interval")) or "5"
    try:
        interval = max(float(raw_interval), 1.0)
    except ValueError:
        interval = 5.0
    return DeviceStart(
        device_auth_id=device_id,
        user_code=user_code,
        interval_s=interval + 3.0,  # the wire's polling safety margin
        verify_url=f"{ISSUER}/codex/device",
    )


async def poll_device(
    client: httpx.AsyncClient,
    start: DeviceStart,
    *,
    sleep: object = None,
) -> OAuthCredential:
    """Poll until the user finishes in the browser; 403/404 mean "not yet"."""
    do_sleep = asyncio.sleep if sleep is None else sleep
    while True:
        response = await client.post(
            f"{ISSUER}/api/accounts/deviceauth/token",
            json={"device_auth_id": start.device_auth_id, "user_code": start.user_code},
            headers={"User-Agent": _user_agent()},
        )
        if response.status_code == 200:
            record = as_record(response.json())
            code = as_str(record.get("authorization_code")) if record is not None else None
            verifier = as_str(record.get("code_verifier")) if record is not None else None
            if record is None or code is None or verifier is None:
                raise SetupFault("error: the device login reply was malformed — try again")
            return await exchange_code(client, code, f"{ISSUER}/deviceauth/callback", verifier)
        if response.status_code not in _DEVICE_PENDING:
            raise SetupFault(
                f"error: device login failed (HTTP {response.status_code})\n"
                "  Start over: sempipe auth login --headless"
            )
        await do_sleep(start.interval_s)  # type: ignore[operator]


async def login_via_browser(
    client: httpx.AsyncClient,
    *,
    open_browser: object = None,
    announce: object = None,
    port: int = CALLBACK_PORT,
) -> OAuthCredential:
    """The PKCE browser flow: local callback server, state check, code exchange.

    The callback server is stdlib ``http.server`` on a daemon thread; the code
    lands in an ``asyncio`` future via ``call_soon_threadsafe`` — same shutdown
    discipline as the stdin pump (nothing can wedge exit).
    """
    import threading
    import webbrowser
    from http.server import BaseHTTPRequestHandler, HTTPServer

    do_open = webbrowser.open if open_browser is None else open_browser
    pkce = generate_pkce()
    state = _b64url(secrets.token_bytes(32))
    redirect_uri = f"http://localhost:{port}/auth/callback"
    loop = asyncio.get_running_loop()
    code_future: asyncio.Future[str] = loop.create_future()

    def resolve(value: str | None, error: str | None) -> None:
        def apply() -> None:
            if code_future.done():
                return
            if error is not None:
                code_future.set_exception(SetupFault(f"error: login failed — {error}"))
            else:
                assert value is not None
                code_future.set_result(value)

        loop.call_soon_threadsafe(apply)

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:
            from urllib.parse import parse_qs, urlparse

            parsed = urlparse(self.path)
            if parsed.path != "/auth/callback":
                self._respond(404, "Not found")
                return
            query = parse_qs(parsed.query)
            error = (query.get("error_description") or query.get("error") or [None])[0]
            code = (query.get("code") or [None])[0]
            returned_state = (query.get("state") or [None])[0]
            if error is not None:
                resolve(None, error)
                self._respond(400, f"Login failed: {error}. You can close this window.")
                return
            if code is None or returned_state != state:
                message = "invalid OAuth state" if code else "missing authorization code"
                resolve(None, message)
                self._respond(400, f"Login failed: {message}. You can close this window.")
                return
            resolve(code, None)
            self._respond(200, "sempipe is logged in. You can close this window.")

        def _respond(self, status: int, text: str) -> None:
            body = text.encode("utf-8")
            self.send_response(status)
            self.send_header("Content-Type", "text/plain; charset=utf-8")
            self.send_header("Content-Length", str(len(body)))
            self.end_headers()
            self.wfile.write(body)

        def log_message(self, format: str, *args: object) -> None:  # noqa: A002
            del format, args  # the CLI narrates; the server stays quiet

    try:
        server = HTTPServer(("localhost", port), Handler)
    except OSError as exc:
        raise SetupFault(
            f"error: couldn't open the login callback port {port} ({exc.strerror or exc})\n"
            "  Something else is using it. Try the headless flow: sempipe auth login --headless"
        ) from exc
    threading.Thread(target=server.serve_forever, name="sempipe-oauth", daemon=True).start()
    try:
        url = authorize_url(redirect_uri, pkce, state)
        if announce is not None:
            announce(url)  # type: ignore[operator]
        with contextlib.suppress(Exception):  # a headless box just uses the printed URL
            do_open(url)  # type: ignore[operator]
        try:
            code = await asyncio.wait_for(code_future, timeout=_LOGIN_TIMEOUT_S)
        except TimeoutError:
            raise SetupFault(
                "error: the login timed out after 5 minutes\n  Run it again: sempipe auth login"
            ) from None
        return await exchange_code(client, code, redirect_uri, pkce.verifier)
    finally:
        server.shutdown()
        server.server_close()


async def _token_request(client: httpx.AsyncClient, form: dict[str, str]) -> object:
    response = await client.post(
        f"{ISSUER}/oauth/token",
        data=form,
        headers={"User-Agent": _user_agent()},
    )
    if response.status_code != 200:
        raise SetupFault(
            f"error: the login token request failed (HTTP {response.status_code})\n"
            "  Fix: sempipe auth login"
        )
    return response.json()


def _user_agent() -> str:
    from sempipe import __version__

    return f"sempipe/{__version__}"


def _jwt_claims(token: str) -> dict[str, object] | None:
    parts = token.split(".")
    if len(parts) != 3:
        return None
    try:
        padded = parts[1] + "=" * (-len(parts[1]) % 4)
        decoded = base64.urlsafe_b64decode(padded)
        parsed: object = json.loads(decoded)
    except (ValueError, json.JSONDecodeError):
        return None
    record = as_record(parsed)
    return dict(record) if record is not None else None


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).decode("ascii").rstrip("=")
