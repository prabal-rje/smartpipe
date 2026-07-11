"""The ChatGPT-plan wire: payload/headers pinned, SSE folding, refresh discipline."""

from __future__ import annotations

import json
import time
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from smartpipe.config.credentials import OAuthCredential, load_oauth, save_oauth
from smartpipe.core.errors import (
    ItemError,
    RetryableError,
    SchemaRejected,
    SetupFault,
    TransportError,
)
from smartpipe.models.base import AudioData, CompletionRequest, ImageData, ModelRef
from smartpipe.models.http_support import make_client
from smartpipe.models.openai_codex import (
    CODEX_ENDPOINT,
    CodexChatModel,
    accumulate_sse,
    build_payload,
)
from smartpipe.models.openai_oauth import ISSUER
from smartpipe.models.retry import RetryPolicy
from tests.helpers.wire import sent_header

if TYPE_CHECKING:
    from collections.abc import AsyncIterator
    from pathlib import Path

FRESH_MS = int(time.time() * 1000) + 3_600_000
STALE_MS = int(time.time() * 1000) - 1_000
FAST = RetryPolicy(attempts=2, base_delay=0.0)


def _sse(*events: dict[str, object]) -> str:
    return "".join(f"data: {json.dumps(event)}\n\n" for event in events) + "data: [DONE]\n\n"


COMPLETED = _sse(
    {"type": "response.output_text.delta", "delta": "hel"},
    {"type": "response.output_text.delta", "delta": "lo"},
    {
        "type": "response.completed",
        "response": {
            "output": [{"type": "message", "content": [{"type": "output_text", "text": "hello"}]}]
        },
    },
)

TOKENS = {"access_token": "at-2", "refresh_token": "rt-2", "expires_in": 3600}


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c


def _model(client: httpx.AsyncClient, store: Path, *, expires_ms: int = FRESH_MS) -> CodexChatModel:
    credential = OAuthCredential(
        access="at-1", refresh="rt-1", expires_ms=expires_ms, account_id="acct"
    )
    save_oauth(store, "openai", credential)
    return CodexChatModel(
        ref=ModelRef("openai", "gpt-5.4"),
        client=client,
        store_path=store,
        credential=credential,
        retry=FAST,
    )


# --- pure pieces -----------------------------------------------------------------


def test_payload_shape_is_pinned() -> None:
    schema: dict[str, object] = {
        "type": "object",
        "properties": {"verdict": {"type": "boolean"}},
        "required": ["verdict"],
        "additionalProperties": False,
    }
    request = CompletionRequest(system="Judge.", user="hi", json_schema=schema)
    payload = build_payload("gpt-5.4", request)
    assert payload["model"] == "gpt-5.4"
    assert payload["instructions"] == "Judge."
    assert payload["stream"] is True and payload["store"] is False  # stateless, both sides
    assert payload["input"] == [{"role": "user", "content": [{"type": "input_text", "text": "hi"}]}]
    assert payload["text"] == {
        "format": {
            "type": "json_schema",
            "name": "smartpipe_output",
            "schema": schema,
            "strict": True,
        }
    }


def test_payload_never_claims_strict_for_an_open_schema() -> None:
    request = CompletionRequest(system=None, user="hi", json_schema={"type": "object"})
    payload = build_payload("gpt-5.4", request)
    from smartpipe.core.jsontools import record_at

    fmt = record_at(payload["text"], "format")
    assert fmt is not None and fmt["strict"] is False


def test_payload_carries_images_as_data_uris() -> None:
    request = CompletionRequest(
        system=None, user="describe", media=(ImageData(b"PNG", "image/png"),)
    )
    from smartpipe.core.jsontools import as_items, as_record

    inputs = as_items(build_payload("gpt-5.4", request)["input"])
    assert inputs is not None
    message = as_record(inputs[0])
    content = as_items(message.get("content")) if message is not None else None
    assert content is not None
    image = as_record(content[1])
    assert image is not None and image.get("type") == "input_image"
    assert str(image.get("image_url")).startswith("data:image/png;base64,")


def test_sse_prefers_the_completedbuild_payload() -> None:
    assert accumulate_sse(COMPLETED) == "hello"


def test_sse_falls_back_to_summed_deltas() -> None:
    deltas_only = _sse(
        {"type": "response.output_text.delta", "delta": "a"},
        {"type": "response.output_text.delta", "delta": "b"},
    )
    assert accumulate_sse(deltas_only) == "ab"


def test_sse_failure_event_is_an_item_error() -> None:
    failed = _sse({"type": "response.failed", "response": {"error": {"message": "boom"}}})
    with pytest.raises(ItemError, match="boom"):
        accumulate_sse(failed)


def test_sse_ignores_garbage_lines() -> None:
    body = "event: noise\ndata: not-json\n\n" + _sse(
        {"type": "response.output_text.delta", "delta": "x"}
    )
    assert accumulate_sse(body) == "x"


# --- the wire ---------------------------------------------------------------------


async def test_complete_pins_headers_and_endpoint(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    route = respx_mock.post(CODEX_ENDPOINT).mock(return_value=httpx.Response(200, text=COMPLETED))
    model = _model(client, tmp_path / "auth.json")
    assert await model.complete(CompletionRequest(system=None, user="hi")) == "hello"
    assert sent_header(route, "authorization") == "Bearer at-1"
    assert sent_header(route, "chatgpt-account-id") == "acct"
    assert sent_header(route, "originator") == "smartpipe"
    assert sent_header(route, "user-agent").startswith("smartpipe/")


async def test_preflight_refuses_unsupported_media_before_the_wire(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    route = respx_mock.post(CODEX_ENDPOINT)
    model = _model(client, tmp_path / "auth.json")
    request = CompletionRequest(
        system=None,
        user="listen",
        media=(AudioData(b"audio", "audio/wav"),),
    )
    with pytest.raises(ItemError, match="can't hear audio"):
        model.preflight(request)
    assert route.call_count == 0


async def test_expired_token_refreshes_first_and_persists(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(f"{ISSUER}/oauth/token").mock(return_value=httpx.Response(200, json=TOKENS))
    route = respx_mock.post(CODEX_ENDPOINT).mock(return_value=httpx.Response(200, text=COMPLETED))
    store = tmp_path / "auth.json"
    model = _model(client, store, expires_ms=STALE_MS)
    assert await model.complete(CompletionRequest(system=None, user="hi")) == "hello"
    assert sent_header(route, "authorization") == "Bearer at-2"  # rotated BEFORE use
    stored = load_oauth(store, "openai")
    assert stored is not None and stored.access == "at-2"  # rotation persisted for other runs


async def test_401_gets_one_refresh_and_retry(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(f"{ISSUER}/oauth/token").mock(return_value=httpx.Response(200, json=TOKENS))
    respx_mock.post(CODEX_ENDPOINT).side_effect = [
        httpx.Response(401),
        httpx.Response(200, text=COMPLETED),
    ]
    model = _model(client, tmp_path / "auth.json")
    assert await model.complete(CompletionRequest(system=None, user="hi")) == "hello"


async def test_persistent_401_is_the_login_expired_screen(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(f"{ISSUER}/oauth/token").mock(return_value=httpx.Response(200, json=TOKENS))
    respx_mock.post(CODEX_ENDPOINT).mock(return_value=httpx.Response(401))
    model = _model(client, tmp_path / "auth.json")
    with pytest.raises(SetupFault, match="smartpipe auth login"):
        await model.complete(CompletionRequest(system=None, user="hi"))


async def test_refresh_failure_is_the_login_expired_screen(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(f"{ISSUER}/oauth/token").mock(return_value=httpx.Response(400))
    model = _model(client, tmp_path / "auth.json", expires_ms=STALE_MS)
    with pytest.raises(SetupFault, match="smartpipe auth login"):
        await model.complete(CompletionRequest(system=None, user="hi"))


async def test_other_statuses_skip_the_item(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    route = respx_mock.post(CODEX_ENDPOINT).mock(return_value=httpx.Response(429, text="slow down"))
    model = _model(client, tmp_path / "auth.json")
    with pytest.raises(RetryableError, match="429"):
        await model.complete(CompletionRequest(system=None, user="hi"))
    assert route.call_count == FAST.attempts


async def test_schema_rejection_is_typed_for_packed_recovery(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CODEX_ENDPOINT).mock(
        return_value=httpx.Response(400, text="Invalid response_format json_schema")
    )
    model = _model(client, tmp_path / "auth.json")
    with pytest.raises(SchemaRejected):
        await model.complete(CompletionRequest(system=None, user="hi"))


async def test_server_errors_are_transport_skips(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    # 5xx is the wire failing, not the content — the circuit breaker counts it
    route = respx_mock.post(CODEX_ENDPOINT).mock(
        return_value=httpx.Response(502, text="bad gateway")
    )
    model = _model(client, tmp_path / "auth.json")
    with pytest.raises(TransportError, match="502"):
        await model.complete(CompletionRequest(system=None, user="hi"))
    assert route.call_count == FAST.attempts


async def test_rate_limit_retries_then_recovers(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    route = respx_mock.post(CODEX_ENDPOINT)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}, text="slow down"),
        httpx.Response(200, text=COMPLETED),
    ]
    model = _model(client, tmp_path / "auth.json")
    assert await model.complete(CompletionRequest(system=None, user="hi")) == "hello"
    assert route.call_count == 2


async def test_connect_exhaustion_is_typed_transport(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    route = respx_mock.post(CODEX_ENDPOINT).mock(side_effect=httpx.ConnectTimeout("offline"))
    model = _model(client, tmp_path / "auth.json")
    with pytest.raises(TransportError, match="ChatGPT wire failed"):
        await model.complete(CompletionRequest(system=None, user="hi"))
    assert route.call_count == FAST.attempts


async def test_refresh_is_single_flight_and_reuses_a_peer_rotation(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    # another process already refreshed and wrote a fresh token to the store;
    # our stale in-memory model should pick THAT up instead of hitting the issuer.
    token_route = respx_mock.post(f"{ISSUER}/oauth/token").mock(
        return_value=httpx.Response(200, json=TOKENS)
    )
    respx_mock.post(CODEX_ENDPOINT).mock(return_value=httpx.Response(200, text=COMPLETED))
    store = tmp_path / "auth.json"
    model = _model(client, store, expires_ms=STALE_MS)  # our copy is stale
    save_oauth(  # a peer rotated underneath us
        store, "openai", OAuthCredential("peer-access", "peer-refresh", FRESH_MS, "acct")
    )
    assert await model.complete(CompletionRequest(system=None, user="hi")) == "hello"
    assert token_route.call_count == 0  # no issuer round-trip — the peer's token was fresh
    assert model.credential.access == "peer-access"


async def test_empty_reply_is_an_item_error(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CODEX_ENDPOINT).mock(return_value=httpx.Response(200, text="data: [DONE]\n\n"))
    model = _model(client, tmp_path / "auth.json")
    with pytest.raises(ItemError, match="empty reply"):
        await model.complete(CompletionRequest(system=None, user="hi"))
