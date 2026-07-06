"""The outliers verb: top_k's mirror, anchored scores, honest exclusions."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from sempipe.core.errors import ExitCode, UsageFault
from sempipe.models.base import ChatModel, ModelRef
from sempipe.verbs.outliers import OutliersRequest, run_outliers

if TYPE_CHECKING:
    from collections.abc import Sequence

VECTORS: dict[str, tuple[float, ...]] = {
    "GET /health 200": (1.0, 0.0),
    "GET /health 200 ok": (0.995, 0.0999),
    "GET /users 200": (0.99, 0.141),
    "kernel: watchdog: soft lockup": (0.0, 1.0),  # the planted weirdo
}


class FakeEmbedding:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-embed")

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]:
        def lookup(text: str) -> tuple[float, ...]:
            for key, vector in VECTORS.items():
                if key in text:
                    return vector
            raise KeyError(text)

        return tuple(lookup(text) for text in texts)


class FakeContext:
    async def chat_model(self, flag: str | None = None) -> ChatModel:
        raise RuntimeError("no chat configured")

    async def embedding_model(self, flag: str | None = None) -> FakeEmbedding:
        return FakeEmbedding()

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def remote_transcriber(self) -> None:
        return None


async def _run(stdin_text: str, count: int = 5) -> tuple[ExitCode, list[dict[str, object]], str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_outliers(
            OutliersRequest(count=count),
            FakeContext(),
            stdin=io.StringIO(stdin_text),
            stdout=out,
        )
    return code, [json.loads(line) for line in out.getvalue().splitlines()], err.getvalue()


async def test_planted_outlier_ranks_first_with_anchored_score() -> None:
    stdin_text = (
        "GET /health 200\nGET /health 200 ok\nGET /users 200\nkernel: watchdog: soft lockup\n"
    )
    code, rows, err = await _run(stdin_text, count=2)
    assert code is ExitCode.OK
    assert rows[0]["text"] == "kernel: watchdog: soft lockup"
    first, second = rows[0]["_distance"], rows[1]["_distance"]
    assert isinstance(first, float) and isinstance(second, float) and first > second
    assert rows[0]["source"] == "line 4"
    assert "median neighbor distance" in err and "x out" in err


async def test_record_shape_mirrors_top_k_for_json_rows() -> None:
    lines = "\n".join(json.dumps({"msg": text, "text": text}) for text in list(VECTORS)[:3])
    # make a 4-row corpus with one weirdo, as records
    stdin_text = (
        lines
        + "\n"
        + json.dumps(
            {"msg": "kernel: watchdog: soft lockup", "text": "kernel: watchdog: soft lockup"}
        )
        + "\n"
    )
    _code, rows, _err = await _run(stdin_text, count=1)
    assert rows[0]["msg"] == "kernel: watchdog: soft lockup"  # original fields survive
    assert "_distance" in rows[0]


async def test_tiny_corpus_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault, match="at least 3 items"):
        await _run("a\nb\n")
