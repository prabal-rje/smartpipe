from __future__ import annotations

import json
import sys
from typing import TYPE_CHECKING, Any

import anthropic
import httpx
import pytest

from sempipe.core.errors import ItemError, SetupFault
from sempipe.models.anthropic_adapter import (
    AnthropicChatModel,
    build_anthropic_chat_model,
    load_anthropic_client,
)
from sempipe.models.base import CompletionRequest, parse_model_ref

if TYPE_CHECKING:
    import respx

ENDPOINT = "https://api.anthropic.com/v1/messages"


def _message_response(text: str, *, stop_reason: str = "end_turn") -> httpx.Response:
    content = [{"type": "text", "text": text}] if text else []
    return httpx.Response(
        200,
        json={
            "id": "msg_1",
            "type": "message",
            "role": "assistant",
            "model": "claude-opus-4-8",
            "content": content,
            "stop_reason": stop_reason,
            "usage": {"input_tokens": 1, "output_tokens": 1},
        },
    )


def _model() -> AnthropicChatModel:
    client: Any = anthropic.AsyncAnthropic(api_key="sk-ant-test")
    return AnthropicChatModel(ref=parse_model_ref("claude-opus-4-8"), client=client)


# --- factory: missing extra & missing key -------------------------------------


def test_missing_extra_is_a_setup_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setitem(sys.modules, "anthropic", None)
    with pytest.raises(SetupFault) as excinfo:
        load_anthropic_client("claude-opus-4-8")
    assert "pip install 'smartpipe[anthropic]'" in str(excinfo.value)


def test_load_client_returns_an_async_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    client = load_anthropic_client("claude-opus-4-8")
    assert hasattr(client, "messages")


def test_build_wires_ref_and_client(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("ANTHROPIC_API_KEY", "sk-ant-test")
    ref = parse_model_ref("claude-opus-4-8")
    model = build_anthropic_chat_model(ref)
    assert model.ref is ref
    assert hasattr(model.client, "messages")


# --- complete() ---------------------------------------------------------------


async def test_complete_is_deterministic_and_otherwise_untuned(
    respx_mock: respx.MockRouter,
) -> None:
    route = respx_mock.post(ENDPOINT).mock(return_value=_message_response("hola"))
    reply = await _model().complete(CompletionRequest(system="sys", user="hello"))
    assert reply == "hola"
    body = json.loads(route.calls.last.request.content)
    assert body["model"] == "claude-opus-4-8"
    assert body["messages"] == [{"role": "user", "content": "hello"}]
    assert body["system"] == "sys"
    assert body["temperature"] == 0.0  # a pipe is a data tool (D36)
    for banned in ("top_p", "top_k", "thinking"):
        assert banned not in body


async def test_system_omitted_when_none(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(ENDPOINT).mock(return_value=_message_response("x"))
    await _model().complete(CompletionRequest(system=None, user="hi"))
    assert "system" not in json.loads(route.calls.last.request.content)


async def test_output_config_present_iff_schema(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(ENDPOINT).mock(return_value=_message_response("{}"))
    schema: dict[str, object] = {"type": "object"}
    await _model().complete(CompletionRequest(system=None, user="x", json_schema=schema))
    body = json.loads(route.calls.last.request.content)
    assert body["output_config"] == {"format": {"type": "json_schema", "schema": schema}}

    await _model().complete(CompletionRequest(system=None, user="x"))
    assert "output_config" not in json.loads(route.calls.last.request.content)


async def test_multiple_text_blocks_are_concatenated(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(ENDPOINT).mock(
        return_value=httpx.Response(
            200,
            json={
                "id": "msg_1",
                "type": "message",
                "role": "assistant",
                "model": "claude-opus-4-8",
                "content": [
                    {"type": "text", "text": "foo"},
                    {"type": "text", "text": "bar"},
                ],
                "stop_reason": "end_turn",
                "usage": {"input_tokens": 1, "output_tokens": 1},
            },
        )
    )
    assert await _model().complete(CompletionRequest(system=None, user="x")) == "foobar"


async def test_refusal_is_an_item_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(ENDPOINT).mock(return_value=_message_response("", stop_reason="refusal"))
    with pytest.raises(ItemError, match="declined this item"):
        await _model().complete(CompletionRequest(system=None, user="x"))


async def test_empty_reply_is_an_item_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(ENDPOINT).mock(return_value=_message_response(""))
    with pytest.raises(ItemError, match="empty reply"):
        await _model().complete(CompletionRequest(system=None, user="x"))


async def test_auth_error_is_the_key_screen(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(ENDPOINT).mock(
        return_value=httpx.Response(401, json={"error": {"message": "invalid x-api-key"}})
    )
    with pytest.raises(SetupFault) as excinfo:
        await _model().complete(CompletionRequest(system=None, user="x"))
    assert "ANTHROPIC_API_KEY" in str(excinfo.value)


async def test_connection_error_is_a_setup_fault(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(ENDPOINT).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(SetupFault, match="can't reach the Anthropic API"):
        await _model().complete(CompletionRequest(system=None, user="x"))


async def test_server_error_is_an_item_error(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(ENDPOINT).mock(
        return_value=httpx.Response(529, json={"error": {"message": "overloaded"}})
    )
    with pytest.raises(ItemError, match="anthropic error 529: overloaded"):
        await _model().complete(CompletionRequest(system=None, user="x"))


async def test_server_error_without_structured_body_falls_back(
    respx_mock: respx.MockRouter,
) -> None:
    respx_mock.post(ENDPOINT).mock(return_value=httpx.Response(500, text="plain text boom"))
    with pytest.raises(ItemError, match="anthropic error 500"):
        await _model().complete(CompletionRequest(system=None, user="x"))
