"""The native Gemini wire (D34): parts, schema dialect, taxonomy, VIDEO."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.core.errors import (
    ItemError,
    RetryableError,
    SchemaRejected,
    SetupFault,
    TransportError,
)
from smartpipe.core.jsontools import as_items, as_record
from smartpipe.models.base import (
    CompletionRequest,
    VideoData,
    parse_model_ref,
)
from smartpipe.models.gemini_native import (
    GeminiNativeChatModel,
    native_base_url,
    to_gemini_schema,
)
from smartpipe.models.http_support import make_client
from smartpipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

    import respx

BASE = "https://generativelanguage.googleapis.com/v1beta"
URL = f"{BASE}/models/gemini-2.5-flash:generateContent"
FAST = RetryPolicy(attempts=2, base_delay=0.0)


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c


def _model(client: httpx.AsyncClient) -> GeminiNativeChatModel:
    return GeminiNativeChatModel(
        ref=parse_model_ref("gemini-2.5-flash"),
        client=client,
        base_url=BASE,
        api_key="g-key",
        retry=FAST,
    )


def _reply(text: str) -> httpx.Response:
    return httpx.Response(200, json={"candidates": [{"content": {"parts": [{"text": text}]}}]})


async def test_video_rides_inline_data_byte_identical(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(URL).mock(return_value=_reply("watched"))
    clip = VideoData(b"\x00\x00\x00 ftypisomfakevideo", "video/mp4")
    reply = await _model(client).complete(
        CompletionRequest(system="be brief", user="what happens?", media=(clip,))
    )
    assert reply == "watched"
    body = as_record(json.loads(route.calls.last.request.content))
    assert body is not None
    assert route.calls.last.request.headers["x-goog-api-key"] == "g-key"
    contents = as_items(body.get("contents"))
    assert contents is not None
    first = as_record(contents[0])
    parts = as_items(first.get("parts")) if first is not None else None
    assert parts is not None and len(parts) == 2
    second = as_record(parts[1])
    inline = as_record(second.get("inline_data")) if second is not None else None
    assert inline is not None
    assert inline.get("mime_type") == "video/mp4"
    encoded = inline.get("data")
    assert isinstance(encoded, str)
    assert base64.b64decode(encoded) == clip.data  # byte-identical, the house bar
    system = as_record(body.get("systemInstruction"))
    assert system is not None  # the system prompt rode along


async def test_schema_translates_to_the_gemini_dialect(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(URL).mock(return_value=_reply('{"status": "paid"}'))
    schema = {
        "type": "object",
        "properties": {"status": {"enum": ["paid", "unpaid"]}, "total": {"type": "number"}},
        "required": ["status", "total"],
        "additionalProperties": False,
    }
    await _model(client).complete(
        CompletionRequest(system=None, user="extract", json_schema=schema)
    )
    body = as_record(json.loads(route.calls.last.request.content))
    config = as_record(body.get("generationConfig")) if body is not None else None
    assert config is not None
    assert config.get("responseMimeType") == "application/json"
    translated = as_record(config.get("responseSchema"))
    assert translated is not None
    assert translated.get("type") == "OBJECT"  # the dialect uppercases
    assert "additionalProperties" not in translated  # unsupported keys dropped
    properties = as_record(translated.get("properties"))
    assert properties is not None
    total = as_record(properties.get("total"))
    assert total is not None and total.get("type") == "NUMBER"


async def test_404_is_the_model_missing_screen(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(URL).mock(
        return_value=httpx.Response(404, json={"error": {"message": "not found"}})
    )
    with pytest.raises(SetupFault, match="doesn't know the model"):
        await _model(client).complete(CompletionRequest(system=None, user="x"))


async def test_401_names_the_key_env(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(401, json={}))
    with pytest.raises(SetupFault, match="GEMINI_API_KEY"):
        await _model(client).complete(CompletionRequest(system=None, user="x"))


async def test_retry_after_is_honored_then_succeeds(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(URL)
    route.side_effect = [
        httpx.Response(429, headers={"Retry-After": "0"}),
        _reply("ok"),
    ]
    assert await _model(client).complete(CompletionRequest(system=None, user="x")) == "ok"
    assert route.call_count == 2


async def test_exhausted_rate_limit_is_a_typed_retryable_error(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(URL).mock(
        return_value=httpx.Response(429, json={"error": {"message": "slow down"}})
    )
    with pytest.raises(RetryableError, match="429"):
        await _model(client).complete(CompletionRequest(system=None, user="x"))
    assert route.call_count == 2


async def test_schema_rejection_is_typed_for_packed_recovery(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"message": "Invalid responseSchema: too many properties"}}
        )
    )
    with pytest.raises(SchemaRejected):
        await _model(client).complete(
            CompletionRequest(system=None, user="x", json_schema={"type": "object"})
        )


def test_native_base_url_derives_from_the_compat_override() -> None:
    assert native_base_url({}) == BASE
    assert (
        native_base_url({"SMARTPIPE_GEMINI_BASE_URL": "https://proxy.corp/v1beta/openai"})
        == "https://proxy.corp/v1beta"
    )


def test_schema_dialect_is_recursive() -> None:
    schema = {
        "type": "object",
        "properties": {"rows": {"type": "array", "items": {"type": "string"}}},
        "required": ["rows"],
    }
    translated = to_gemini_schema(schema)
    rows = as_record(as_record(translated["properties"]).get("rows"))  # type: ignore[arg-type]
    assert rows is not None
    items = as_record(rows.get("items"))
    assert items is not None and items.get("type") == "STRING"


async def test_usage_metadata_feeds_the_meter(
    respx_mock: respx.MockRouter, client: httpx.AsyncClient
) -> None:
    from smartpipe.io import metering

    metering.reset()
    respx_mock.post(url__regex=r".*generateContent.*").mock(
        return_value=httpx.Response(
            200,
            json={
                "candidates": [{"content": {"parts": [{"text": "hola"}]}}],
                "usageMetadata": {"promptTokenCount": 90, "candidatesTokenCount": 7},
            },
        )
    )
    await _model(client).complete(CompletionRequest(system="s", user="u"))
    view = metering.snapshot()
    assert (view.tokens_in, view.tokens_out) == (90, 7)


def test_nullable_union_becomes_the_nullable_flag() -> None:
    # D48: {type: [string, null]} → Gemini dialect {type: STRING, nullable: true}
    assert to_gemini_schema({"type": ["string", "null"]}) == {"type": "STRING", "nullable": True}


def test_scalar_union_drops_type_but_keeps_shape() -> None:
    # no dialect equivalent for a multi-type union - local validation guards it
    out = to_gemini_schema({"type": ["string", "number", "integer", "boolean"]})
    assert "type" not in out and "nullable" not in out


async def test_server_error_after_retries_is_a_transport_skip(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    # 5xx that survives the retries is the wire failing — the breaker counts it
    route = respx_mock.post(URL).mock(
        return_value=httpx.Response(503, json={"error": {"message": "overloaded"}})
    )
    with pytest.raises(TransportError, match="gemini error 503"):
        await _model(client).complete(CompletionRequest(system=None, user="x"))
    assert route.call_count == 2  # FAST retries exhausted first


async def test_connect_exhaustion_is_a_transport_error(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(URL).mock(side_effect=httpx.ConnectError("offline"))
    with pytest.raises(TransportError, match="can't reach"):
        await _model(client).complete(CompletionRequest(system=None, user="x"))
    assert route.call_count == 2


async def test_malformed_success_json_is_an_item_error(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(200, text="not-json"))
    with pytest.raises(ItemError, match="Gemini returned malformed JSON"):
        await _model(client).complete(CompletionRequest(system=None, user="x"))
