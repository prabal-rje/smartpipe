"""Vision wire shapes, byte-verified: each adapter must carry the image so the
provider can decode it back to the original bytes. Capability failures skip."""

from __future__ import annotations

import base64
from typing import TYPE_CHECKING

import httpx
import pytest
import respx

from smartpipe.core.errors import ItemError
from smartpipe.core.jsontools import as_items, as_record, as_str
from smartpipe.models.base import CompletionRequest, ImageData, ModelRef
from smartpipe.models.http_support import make_client
from smartpipe.models.ollama import OllamaChatModel
from smartpipe.models.openai_compat import OpenAIChatModel
from tests.helpers.wire import sent_json

if TYPE_CHECKING:
    from collections.abc import AsyncIterator

PNG_BYTES = b"\x89PNG\r\n\x1a\nFAKEDATA"
IMAGE = ImageData(data=PNG_BYTES, mime="image/png")


def _vision_request() -> CompletionRequest:
    return CompletionRequest(system="Describe.", user="Describe this.", media=(IMAGE,))


@pytest.fixture
async def client() -> AsyncIterator[httpx.AsyncClient]:
    async with make_client() as c:
        yield c


async def test_ollama_sends_base64_images(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(200, json={"message": {"content": "a cat"}})
    )
    model = OllamaChatModel(
        ref=ModelRef("ollama", "qwen3-vl"), client=client, host="http://localhost:11434"
    )
    assert await model.complete(_vision_request()) == "a cat"
    body = as_record(sent_json(route))
    assert body is not None
    messages = as_items(body.get("messages"))
    assert messages
    user = as_record(messages[-1])
    assert user is not None
    images = as_items(user.get("images"))
    assert images is not None
    first = as_str(images[0])
    assert first is not None
    assert base64.b64decode(first) == PNG_BYTES  # byte-roundtrip, not "looks right"
    assert user.get("content") == "Describe this."


async def test_ollama_vision_400_becomes_a_skip(
    client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    respx_mock.post("http://localhost:11434/api/chat").mock(
        return_value=httpx.Response(400, json={"error": "model does not support images"})
    )
    model = OllamaChatModel(
        ref=ModelRef("ollama", "qwen3:8b"), client=client, host="http://localhost:11434"
    )
    with pytest.raises(ItemError, match="vision model"):
        await model.complete(_vision_request())


@pytest.mark.parametrize(
    ("base", "ref"),
    [
        ("https://api.openai.com", ModelRef("openai", "gpt-4o-mini")),
        # pixtral takes the exact same image_url data-URI content array (workstream 10)
        ("https://api.mistral.ai", ModelRef("mistral", "pixtral-12b-latest")),
    ],
    ids=["openai", "mistral"],
)
async def test_openai_wire_sends_a_data_uri_content_array(
    base: str, ref: ModelRef, client: httpx.AsyncClient, respx_mock: respx.MockRouter
) -> None:
    route = respx_mock.post(f"{base}/v1/chat/completions").mock(
        return_value=httpx.Response(200, json={"choices": [{"message": {"content": "a cat"}}]})
    )
    model = OpenAIChatModel(
        ref=ref,
        client=client,
        base_url=base,
        api_key="sk-test",
    )
    assert await model.complete(_vision_request()) == "a cat"
    body = as_record(sent_json(route))
    assert body is not None
    messages = as_items(body.get("messages"))
    assert messages
    user = as_record(messages[-1])
    assert user is not None
    parts = as_items(user.get("content"))
    assert parts is not None and parts[0] == {"type": "text", "text": "Describe this."}
    image_part = as_record(parts[1])
    image_url = as_record(image_part.get("image_url")) if image_part is not None else None
    url = as_str(image_url.get("url")) if image_url is not None else None
    assert url is not None
    prefix, b64 = url.split(";base64,", 1)
    assert prefix == "data:image/png"
    assert base64.b64decode(b64) == PNG_BYTES


def test_anthropic_builds_image_blocks_image_first() -> None:
    from smartpipe.models.anthropic_adapter import build_kwargs

    kwargs = build_kwargs("claude-opus-4-8", _vision_request())
    messages = as_items(kwargs.get("messages"))
    assert messages
    message = as_record(messages[0])
    assert message is not None
    content = as_items(message.get("content"))
    assert content is not None
    image_block = as_record(content[0])
    assert image_block is not None and image_block.get("type") == "image"
    source = as_record(image_block.get("source"))
    assert source is not None and source.get("media_type") == "image/png"
    data = as_str(source.get("data"))
    assert data is not None and base64.b64decode(data) == PNG_BYTES
    assert content[1] == {"type": "text", "text": "Describe this."}


def test_plain_requests_are_unchanged() -> None:
    from smartpipe.models.anthropic_adapter import build_kwargs

    kwargs = build_kwargs("claude-opus-4-8", CompletionRequest(system=None, user="hi"))
    assert kwargs["messages"] == [{"role": "user", "content": "hi"}]  # still the string form
