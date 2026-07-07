"""The Jina media-native embedding wire (D39/04)."""

from __future__ import annotations

import base64
import json
from typing import TYPE_CHECKING

import httpx
import pytest

from smartpipe.core.errors import SetupFault
from smartpipe.models.base import ImageData, ModelRef, supports_media_embedding
from smartpipe.models.jina import JinaClipEmbeddingModel
from smartpipe.models.retry import RetryPolicy

if TYPE_CHECKING:
    import respx

FAST = RetryPolicy(attempts=1, base_delay=0.0)
URL = "https://api.jina.ai/v1/embeddings"


def _model(client: httpx.AsyncClient) -> JinaClipEmbeddingModel:
    return JinaClipEmbeddingModel(
        ref=ModelRef("jina", "jina-clip-v2"), client=client, api_key="jk-x", retry=FAST
    )


async def test_mixed_parts_payload_and_index_order(respx_mock: respx.MockRouter) -> None:
    route = respx_mock.post(URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [  # out of order on purpose — index wins
                    {"index": 1, "embedding": [0.0, 1.0]},
                    {"index": 0, "embedding": [1.0, 0.0]},
                ]
            },
        )
    )
    async with httpx.AsyncClient() as client:
        vectors = await _model(client).embed_parts(["a caption", ImageData(b"pixels", "image/png")])
    assert vectors == ((1.0, 0.0), (0.0, 1.0))
    payload = json.loads(route.calls.last.request.content)
    assert payload == {
        "model": "jina-clip-v2",
        "input": [
            {"text": "a caption"},
            {"image": base64.b64encode(b"pixels").decode("ascii")},
        ],
    }


async def test_401_names_the_env_var(respx_mock: respx.MockRouter) -> None:
    respx_mock.post(URL).mock(return_value=httpx.Response(401, text="no"))
    async with httpx.AsyncClient() as client:
        with pytest.raises(SetupFault, match="JINA_API_KEY"):
            await _model(client).embed(["x"])


def test_the_wire_is_media_capable_and_compat_is_not() -> None:
    from smartpipe.models.openai_compat import OpenAIEmbeddingModel

    async def check() -> tuple[bool, bool]:
        async with httpx.AsyncClient() as client:
            jina = _model(client)
            compat = OpenAIEmbeddingModel(
                ref=ModelRef("openai", "text-embedding-3-small"),
                client=client,
                base_url="https://api.openai.com",
                api_key="sk-x",
            )
            return supports_media_embedding(jina), supports_media_embedding(compat)

    import asyncio

    media_capable, text_only = asyncio.run(check())
    assert media_capable is True
    assert text_only is False  # the mention IS the switch — compat never routes media
