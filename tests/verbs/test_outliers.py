"""The outliers verb: top_k's mirror, anchored scores, honest exclusions."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.models.base import ChatModel, ModelRef
from smartpipe.verbs.outliers import OutliersRequest, run_outliers

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

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
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
    first, second = rows[0]["__distance"], rows[1]["__distance"]
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
    assert "__distance" in rows[0]


async def test_tiny_corpus_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault, match="at least 3 items"):
        await _run("a\nb\n")


# --- the ocr-model role at ingestion (item 48) ---------------------------------------


async def test_ocr_role_parses_pattern_scans_at_ingestion(tmp_path: object) -> None:
    import contextlib
    import pathlib

    from smartpipe.io.inputs import InputSpec
    from tests.io.test_ocr_ingest import FakeParser

    base = pathlib.Path(str(tmp_path))
    parser = FakeParser(image_text="kernel: watchdog: soft lockup")

    class OcrContext(FakeContext):
        def document_parser(self, flag: str | None = None) -> FakeParser:  # type: ignore[override]
            return parser

    (base / "a.txt").write_text("GET /health 200", encoding="utf-8")
    (base / "b.txt").write_text("GET /users 200", encoding="utf-8")
    (base / "scan.png").write_bytes(b"\x89PNG\r\n\x1a\nfake")

    class _Tty(io.StringIO):
        def isatty(self) -> bool:
            return True

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_outliers(
            OutliersRequest(
                count=1, input=InputSpec(patterns=(str(base / "*"),), from_files=False)
            ),
            OcrContext(),
            stdin=_Tty(),
            stdout=out,
        )
    assert code is ExitCode.OK
    assert len(parser.image_calls) == 1
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    assert rows[0]["text"] == "kernel: watchdog: soft lockup"  # the parsed scan IS the weirdo
    assert "parsed by mistral/mistral-ocr-latest" in err.getvalue()
