from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode, ItemError
from sempipe.models.base import ChatModel, ModelRef
from sempipe.verbs.embed import EmbedRequest, run_embed

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
        from sempipe.core.errors import SetupFault

        raise SetupFault("no chat configured — the converter takes the lower rungs")

    def concurrency(self, flag: int | None = None) -> int:
        return 2


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
    assert json.loads(lines[0]) == {"text": "ab", "vector": [2.0, 1.0], "source": "-"}
    assert json.loads(lines[1]) == {"text": "cde", "vector": [3.0, 1.0], "source": "-"}


async def test_always_ndjson_even_for_one_item() -> None:
    _code, out, _model = await _run("x\n")
    assert out == '{"text":"x","vector":[1.0,1.0],"source":"-"}\n'


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
    monkeypatch.setattr("sempipe.io.tty.stdout_is_tty", lambda: True)
    await _run("x\n")
    assert "redirect to a file" in capsys.readouterr().err
