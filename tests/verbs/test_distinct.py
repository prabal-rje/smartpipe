"""The distinct verb: exact folds free, near folds by meaning, silence discloses."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode
from sempipe.models.base import ModelRef

if True:  # runtime import for the Protocol annotation
    from sempipe.models.base import ChatModel
from sempipe.verbs.distinct import DistinctRequest, run_distinct

if TYPE_CHECKING:
    from collections.abc import Sequence


VECTORS: dict[str, tuple[float, ...]] = {
    "app crashes when saving": (1.0, 0.0),
    "saving crashes the app!!": (0.995, 0.0999),  # near-dup of the first
    "dark mode please": (0.0, 1.0),
}


class FakeEmbedding:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.seen: list[str] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.seen.extend(texts)
        return tuple(VECTORS[text] for text in texts)


class FakeContext:
    def __init__(self) -> None:
        self.embedder = FakeEmbedding()

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        raise RuntimeError("no chat configured")  # optional_chat handles this

    async def embedding_model(self, flag: str | None = None) -> FakeEmbedding:
        return self.embedder

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None


async def _run(stdin_text: str, **kwargs: object) -> tuple[ExitCode, str, str, FakeContext]:
    import contextlib

    context = FakeContext()
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_distinct(
            DistinctRequest(**kwargs),  # type: ignore[arg-type]
            context,
            stdin=io.StringIO(stdin_text),
            stdout=out,
        )
    return code, out.getvalue(), err.getvalue(), context


async def test_exact_duplicates_fold_before_any_embedding() -> None:
    stdin_text = "dark mode please\ndark mode please\ndark mode please\n"
    code, out, err, context = await _run(stdin_text)
    assert code is ExitCode.OK
    assert out == "dark mode please\n"
    assert context.embedder.seen == ["dark mode please"]  # one embed, not three
    assert "kept 1 of 3 (2 exact + 0 near duplicates folded)" in err


async def test_near_duplicates_fold_by_meaning_first_wins() -> None:
    stdin_text = "app crashes when saving\nsaving crashes the app!!\ndark mode please\n"
    code, out, err, _context = await _run(stdin_text)
    assert code is ExitCode.OK
    assert out == "app crashes when saving\ndark mode please\n"  # order + bytes kept
    assert "kept 2 of 3 (0 exact + 1 near duplicates folded)" in err


async def test_show_groups_is_the_audit_trail() -> None:
    stdin_text = "app crashes when saving\nsaving crashes the app!!\napp crashes when saving\n"
    _code, out, _err, _context = await _run(stdin_text, show_groups=True)
    rows = [json.loads(line) for line in out.splitlines()]
    assert rows == [
        {
            "kept": "app crashes when saving",
            "count": 3,
            "duplicates": ["saving crashes the app!!", "app crashes when saving"],
        }
    ]


async def test_empty_input_is_ok_and_silent() -> None:
    code, out, _err, context = await _run("")
    assert code is ExitCode.OK
    assert out == ""
    assert context.embedder.seen == []


async def test_bad_threshold_is_a_usage_fault() -> None:
    import pytest

    from sempipe.core.errors import UsageFault

    with pytest.raises(UsageFault, match="between 0 and 1"):
        await _run("x\n", threshold=1.5)


# --- native media embeddings (D39/04) ----------------------------------------------


async def test_image_only_items_route_natively_no_captions() -> None:
    import base64
    import contextlib
    import io as io_module
    import json

    from sempipe.core.errors import ExitCode
    from sempipe.models.base import ImageData
    from sempipe.models.base import ImageData as ImagePart

    class MediaEmbedder:
        """jina-shaped: embed_parts marks it media-capable."""

        def __init__(self) -> None:
            self.ref = ModelRef("jina", "jina-clip-v2")
            self.part_calls: list[str | ImagePart] = []

        async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("image-only items must not reach the text path")

        async def embed_parts(
            self, parts: Sequence[str | ImagePart]
        ) -> tuple[tuple[float, ...], ...]:
            self.part_calls.extend(parts)
            return tuple((1.0, 0.0) for _part in parts)

    class MediaContext:
        def __init__(self) -> None:
            self.media_embedder = MediaEmbedder()

        async def chat_model(self, flag: str | None = None) -> ChatModel:
            raise RuntimeError("no chat configured")

        async def embedding_model(self, flag: str | None = None) -> MediaEmbedder:
            return self.media_embedder

        def concurrency(self, flag: int | None = None) -> int:
            return 2

        def remote_transcriber(self, chat_ref: object | None = None) -> None:
            return None

    line = json.dumps(
        {"image_b64": base64.b64encode(b"pixels").decode(), "mime": "image/png", "source": "a.png"}
    )
    context = MediaContext()
    out = io_module.StringIO()
    err = io_module.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_distinct(
            DistinctRequest(),
            context,
            stdin=io_module.StringIO(line + "\n"),
            stdout=out,
        )
    assert code is ExitCode.OK
    assert len(context.media_embedder.part_calls) == 1
    assert isinstance(context.media_embedder.part_calls[0], ImageData)  # bytes, not a caption
    assert "media embedded natively (jina/jina-clip-v2)" in err.getvalue()
