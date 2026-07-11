from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, ItemError
from smartpipe.engine.runner import FailurePolicy
from smartpipe.models.base import ChatModel, ModelRef
from smartpipe.verbs.embed import EmbedRequest, run_embed

if TYPE_CHECKING:
    from collections.abc import Sequence


class FakeEmbed:
    """Deterministic embeddings: each text → a fixed 2-vector derived from its length."""

    def __init__(self, *, fail_text: str | None = None) -> None:
        self.ref = ModelRef("ollama", "fake-embed")
        self.fail_text = fail_text
        self.batches: list[list[str]] = []

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        self.batches.append(list(texts))
        out: list[tuple[float, ...]] = []
        for text in texts:
            if text == self.fail_text:
                raise ItemError("embedding failed")
            out.append((float(len(text)), 1.0))
        return tuple(out)


class FakeContext:
    def __init__(self, model: FakeEmbed) -> None:
        self.model = model

    async def embedding_model(self, flag: str | None = None) -> FakeEmbed:
        return self.model

    async def chat_model(self, flag: str | None = None) -> ChatModel:
        from smartpipe.core.errors import SetupFault

        raise SetupFault("no chat configured — the converter takes the lower rungs")

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def failure_policy(self, provider: str) -> FailurePolicy:
        return FailurePolicy(transport_limit=5, transport_screen=f"{provider} unavailable")

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None

    def document_parser(self, flag: str | None = None) -> None:
        return None

    async def media_embedding_model(self, flag: str | None = None) -> None:
        return None


async def _run(stdin: str, *, fail_text: str | None = None) -> tuple[ExitCode, str, FakeEmbed]:
    model = FakeEmbed(fail_text=fail_text)
    out = io.StringIO()
    code = await run_embed(
        EmbedRequest(model_flag=None, concurrency_flag=None),
        FakeContext(model),
        stdin=io.StringIO(stdin),
        stdout=out,
    )
    return code, out.getvalue(), model


async def test_emits_ndjson_record_per_item_in_order() -> None:
    code, out, _model = await _run("ab\ncde\n")
    assert code == ExitCode.OK
    lines = out.splitlines()
    assert json.loads(lines[0]) == {
        "text": "ab",
        "vector": [2.0, 1.0],
        "__embedder": "ollama/fake-embed",
        "__source": {"path": "-", "as": "lines", "line": 1},
    }
    assert json.loads(lines[1]) == {
        "text": "cde",
        "vector": [3.0, 1.0],
        "__embedder": "ollama/fake-embed",
        "__source": {"path": "-", "as": "lines", "line": 2},
    }


async def test_always_ndjson_even_for_one_item() -> None:
    _code, out, _model = await _run("x\n")
    assert out == (
        '{"text":"x","vector":[1.0,1.0],"__embedder":"ollama/fake-embed",'
        '"__source":{"path":"-","as":"lines","line":1}}\n'
    )


async def test_reader_fed_record_embeds_its_text_not_the_wrapper() -> None:
    """Deliverable 4 pin: `smartpipe FILE | smartpipe embed` must embed the
    record's meaningful text — never the serialized JSON, spine and all."""
    spine = '"__source": {"path": "notes.txt", "as": "lines", "line": 7}'
    wrapped = '{"text": "hello world", ' + spine + "}\n"
    _code, out_wrapped, model_wrapped = await _run(wrapped)
    _code2, _out_direct, model_direct = await _run("hello world\n")
    assert model_wrapped.batches == model_direct.batches  # identical text reached the wire
    record = json.loads(out_wrapped)
    assert record["text"] == "hello world"
    assert record["__source"] == {"path": "notes.txt", "as": "lines", "line": 7}


async def test_empty_input_is_ok_silent() -> None:
    code, out, model = await _run("")
    assert code == ExitCode.OK
    assert out == ""
    assert model.batches == []


async def test_failed_item_is_skipped(capsys: pytest.CaptureFixture[str]) -> None:
    code, out, _model = await _run("good\nbad\nalso good\n", fail_text="bad")
    assert code == ExitCode.PARTIAL
    assert [json.loads(line)["text"] for line in out.splitlines()] == ["good", "also good"]
    assert "skipped: line 2" in capsys.readouterr().err


async def test_all_failed_is_exit_3() -> None:
    code, out, _model = await _run("bad\n", fail_text="bad")
    assert code == ExitCode.ALL_FAILED
    assert out == ""


async def test_tty_stdout_gets_a_redirect_note(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("smartpipe.io.tty.stdout_is_tty", lambda: True)
    await _run("x\n")
    assert "redirect to a file" in capsys.readouterr().err


async def test_streaming_path_embeds_images_natively() -> None:
    """The first live jina call caption-pivoted: only the finite-corpus branch
    checked the native route. The streaming worker must route pixels too."""
    from smartpipe.io import diagnostics
    from smartpipe.io.items import Item, ItemSource
    from smartpipe.models.base import ImageData
    from smartpipe.verbs import embed as embed_module
    from smartpipe.verbs.convert import make_converter

    _embed_one = embed_module._embed_one  # pyright: ignore[reportPrivateUsage] — worker under test

    class FakeClip:
        ref = ModelRef("jina", "jina-clip-v2")

        def __init__(self) -> None:
            self.calls: list[object] = []

        async def embed(self, texts: object) -> tuple[tuple[float, ...], ...]:
            raise AssertionError("text path must not run for an image-only item")

        async def embed_parts(self, parts: object) -> tuple[tuple[float, ...], ...]:
            self.calls.append(parts)
            return ((0.1, 0.2),)

    item = Item(
        raw="",
        text="",
        data=None,
        source=ItemSource(kind="file", name="p.png", index=0),
        media=(ImageData(b"\x89PNG", "image/png"),),
    )
    model = FakeClip()
    log = diagnostics.DegradationLog()
    converter = make_converter(None, allow_paid=False, log=log, stt=None)
    _out, vector = await _embed_one(model, item, log, converter)
    assert vector == (0.1, 0.2)
    assert model.calls  # pixels reached the media embedder, not the caption rung
