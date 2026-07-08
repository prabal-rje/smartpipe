"""The ``media-embed-model`` role (ledger item 40, deliverable 2).

When configured, media items route to the joint-space embedder while text
items keep ``embed-model``; one vector space per run is the law, enforced by
the geometry fence before any spend.
"""

from __future__ import annotations

import base64
import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.io.items import item_from_line
from smartpipe.models.base import ChatModel, ImageData, ModelRef
from smartpipe.verbs.common import GeometryFence, media_embedder, row_embedder
from smartpipe.verbs.embed import EmbedRequest, run_embed
from smartpipe.verbs.top_k import TopKRequest, run_top_k

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeText:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.batches: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.batches.append(list(texts))
        return tuple((float(len(text)), 1.0) for text in texts)


class FakeClip:
    """A joint text+image embedder (structurally a MediaEmbeddingModel)."""

    def __init__(self) -> None:
        self.ref = ModelRef("jina", "jina-clip-v2")
        self.parts: list[list[object]] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        return await self.embed_parts(list(texts))

    async def embed_parts(self, parts: Sequence[str | ImageData]) -> tuple[tuple[float, ...], ...]:
        self.parts.append(list(parts))
        return tuple((7.0, 7.0) for _ in parts)


class RoleContext:
    def __init__(self, model: FakeText | FakeClip, media_model: FakeClip | None) -> None:
        self.model = model
        self.media_model = media_model

    async def embedding_model(self, flag: str | None = None) -> FakeText | FakeClip:
        return self.model

    async def media_embedding_model(self, flag: str | None = None) -> FakeClip | None:
        return self.media_model

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        from smartpipe.core.errors import SetupFault

        raise SetupFault("no chat configured")

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def document_parser(self, flag: str | None = None) -> None:
        return None


def _image_row() -> str:
    payload = base64.b64encode(b"\x89PNGfake").decode("ascii")
    media = {"kind": "image", "mime": "image/png", "data_b64": payload}
    return json.dumps({"text": "", "__media": media})


async def _run_embed(
    stdin: str, model: FakeText | FakeClip, media_model: FakeClip | None
) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_embed(
        EmbedRequest(model_flag=None, concurrency_flag=None),
        RoleContext(model, media_model),
        stdin=io.StringIO(stdin),
        stdout=out,
    )
    return code, out.getvalue()


async def test_media_items_route_to_the_media_role() -> None:
    text_model = FakeText()
    clip = FakeClip()
    code, out = await _run_embed(_image_row() + "\n", text_model, clip)
    assert code == ExitCode.OK
    assert clip.parts and isinstance(clip.parts[0][0], ImageData)  # pixels, not captions
    assert text_model.batches == []  # the text model never saw the image
    assert json.loads(out.strip())["__embedder"] == "jina/jina-clip-v2"


async def test_text_items_keep_the_embed_model() -> None:
    text_model = FakeText()
    clip = FakeClip()
    code, out = await _run_embed("plain words\n", text_model, clip)
    assert code == ExitCode.OK
    assert text_model.batches == [["plain words"]]
    assert clip.parts == []
    assert json.loads(out.strip())["__embedder"] == "ollama/fake-embed"


async def test_mixed_kinds_with_split_roles_is_a_loud_usage_fault() -> None:
    with pytest.raises(UsageFault) as caught:
        await _run_embed("plain words\n" + _image_row() + "\n", FakeText(), FakeClip())
    message = str(caught.value)
    assert "ollama/fake-embed" in message
    assert "jina/jina-clip-v2" in message
    assert "one vector space" in message


async def test_mixed_kinds_with_the_joint_model_everywhere_passes() -> None:
    joint = FakeClip()
    media_role = FakeClip()  # same ref — one space, no fence
    code, out = await _run_embed("plain words\n" + _image_row() + "\n", joint, media_role)
    assert code == ExitCode.OK
    stamps = {json.loads(line)["__embedder"] for line in out.splitlines()}
    assert stamps == {"jina/jina-clip-v2"}


async def test_top_k_query_follows_a_media_corpus_into_the_joint_space() -> None:
    text_model = FakeText()
    clip = FakeClip()
    out = io.StringIO()
    request = TopKRequest(
        near="a red square",
        k=1,
        threshold=None,
        model_flag=None,
        concurrency_flag=None,
    )
    code = await run_top_k(
        request,
        RoleContext(text_model, clip),
        stdin=io.StringIO(_image_row() + "\n"),
        stdout=out,
    )
    assert code == ExitCode.OK
    assert ["a red square"] in clip.parts  # the query embedded in the JOINT space
    assert text_model.batches == []


def test_geometry_fence_only_trips_on_both_kinds_with_split_refs() -> None:
    fence = GeometryFence(text_ref="a/x", media_ref="b/y")
    fence.admit(media=False)
    fence.admit(media=False)  # text alone never trips
    with pytest.raises(UsageFault):
        fence.admit(media=True)
    same = GeometryFence(text_ref="a/x", media_ref="a/x")
    same.admit(media=False)
    same.admit(media=True)  # equal refs can never trip


def test_row_embedder_stamps_by_route() -> None:
    text_model = FakeText()
    clip = FakeClip()
    assert media_embedder(text_model, None) is text_model
    assert media_embedder(text_model, clip) is clip
    text_item = item_from_line("words", 0)
    image_item = item_from_line(_image_row(), 1)
    assert row_embedder(text_item, text_model, clip) == "ollama/fake-embed"
    assert row_embedder(image_item, text_model, clip) == "jina/jina-clip-v2"
    assert row_embedder(image_item, text_model, None) == "ollama/fake-embed"  # role unset
