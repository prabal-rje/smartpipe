"""The diff verb: lopsided themes with shares as evidence."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.models.base import CompletionRequest, ModelRef
from smartpipe.verbs.diff import DiffRequest, run_diff

if TYPE_CHECKING:
    from collections.abc import Sequence
    from pathlib import Path

VECTORS: dict[str, tuple[float, ...]] = {
    "timeout calling payments-v2": (1.0, 0.0),
    "payments-v2 upstream 504": (0.99, 0.141),
    "disk full on node-3": (0.0, 1.0),
    "disk usage warning node-3": (0.05, 0.999),
    "healthcheck ok": (0.7, 0.714),
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


class NamesThemes:
    def __init__(self) -> None:
        self.ref = ModelRef("ollama", "fake-chat")

    async def complete(self, request: CompletionRequest) -> str:
        label = "payments-v2 timeouts" if "payments" in request.user else "disk pressure"
        return json.dumps({"label": label})


class FakeContext:
    async def chat_model(self, flag: str | None = None) -> NamesThemes:
        return NamesThemes()

    async def embedding_model(self, flag: str | None = None) -> FakeEmbedding:
        return FakeEmbedding()

    def concurrency(self, flag: int | None = None) -> int:
        return 2

    def document_parser(self, flag: str | None = None) -> None:
        return None

    def remote_transcriber(self, chat_ref: object | None = None) -> None:
        return None


async def _run(
    left: str, right_lines: str, tmp_path: Path, **kwargs: object
) -> tuple[ExitCode, list[dict[str, object]], str]:
    import contextlib

    right = tmp_path / "before.log"
    right.write_text(right_lines, encoding="utf-8")
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_diff(
            DiffRequest(right=right, **kwargs),  # type: ignore[arg-type]
            FakeContext(),
            stdin=io.StringIO(left),
            stdout=out,
        )
    return code, [json.loads(line) for line in out.getvalue().splitlines()], err.getvalue()


LEFT = "timeout calling payments-v2\npayments-v2 upstream 504\nhealthcheck ok\n"
RIGHT = "disk full on node-3\ndisk usage warning node-3\nhealthcheck ok\n"


async def test_lopsided_themes_carry_sides_and_shares(tmp_path: Path) -> None:
    code, rows, err = await _run(LEFT, RIGHT, tmp_path)
    assert code is ExitCode.OK
    by_side = {row["side"]: row for row in rows}
    assert by_side["left"]["theme"] == "payments-v2 timeouts"
    assert by_side["left"]["share_left"] == 0.67
    assert by_side["left"]["share_right"] == 0.0
    assert by_side["right"]["theme"] == "disk pressure"
    # item 64: theme rows carry a summary spine - side + member count
    assert by_side["left"]["__source"] == {"as": "diff", "side": "left", "count": 2}
    assert by_side["right"]["__source"] == {"as": "diff", "side": "right", "count": 2}
    assert "left = stdin (3)" in err  # provenance + preview line
    assert "1 shared theme(s) omitted" in err  # healthcheck lives on both sides


async def test_all_includes_shared_themes(tmp_path: Path) -> None:
    _code, rows, _err = await _run(LEFT, RIGHT, tmp_path, show_all=True)
    assert any(row["side"] == "both" for row in rows)


async def test_empty_side_is_a_usage_fault(tmp_path: Path) -> None:
    with pytest.raises(UsageFault, match="BOTH sides"):
        await _run("", RIGHT, tmp_path)


# --- the ocr-model role at ingestion (item 48) ---------------------------------------


async def test_right_pdf_parses_through_the_configured_role(tmp_path: Path) -> None:
    import contextlib

    from smartpipe.models.ocr import OcrPage

    class DiskParser:
        ref = ModelRef("mistral", "mistral-ocr-latest")

        def __init__(self) -> None:
            self.pdf_calls: list[object] = []

        async def parse_pdf(self, path: object) -> tuple[OcrPage, ...]:
            self.pdf_calls.append(path)
            return (
                OcrPage(0, "disk full on node-3"),
                OcrPage(1, "disk usage warning node-3"),
            )

        async def parse_image(self, image: object) -> str:
            raise AssertionError("a PDF right side never reaches the image wire")

    parser = DiskParser()

    class OcrContext(FakeContext):
        def document_parser(self, flag: str | None = None) -> DiskParser:  # type: ignore[override]
            return parser

    right = tmp_path / "before.pdf"
    right.write_bytes(b"%PDF-1.4 tiny")
    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = await run_diff(
            DiffRequest(right=right),
            OcrContext(),
            stdin=io.StringIO(LEFT),
            stdout=out,
        )
    assert code is ExitCode.OK
    assert parser.pdf_calls  # the right side parsed through the role
    rows = [json.loads(line) for line in out.getvalue().splitlines()]
    by_side = {row["side"]: row for row in rows}
    assert by_side["right"]["theme"] == "disk pressure"  # pages became right items
    assert "parsed by mistral/mistral-ocr-latest" in err.getvalue()
