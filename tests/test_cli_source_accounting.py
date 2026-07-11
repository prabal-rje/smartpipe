"""End-to-end source accounting across ingestion and OCR work units."""

from __future__ import annotations

import json
from pathlib import Path
from typing import IO, TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli
from tests.helpers.pdf import minimal_pdf

if TYPE_CHECKING:
    import respx

CHAT = "http://localhost:11434/api/chat"
EMBED = "http://localhost:11434/api/embed"
OCR = "https://api.mistral.ai/v1/ocr"


@pytest.mark.parametrize("surface", ("reader", "map"))
def test_unreadable_named_source_is_counted_as_a_failed_skip(
    surface: str,
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    good = tmp_path / "good.txt"
    bad = tmp_path / "bad.txt"
    good.write_text("good\n", encoding="utf-8")
    bad.write_text("private\n", encoding="utf-8")
    original_open = Path.open

    def guarded_open(
        path: Path,
        mode: str = "r",
        buffering: int = -1,
        encoding: str | None = None,
        errors: str | None = None,
        newline: str | None = None,
    ) -> IO[str] | IO[bytes]:
        if path.resolve() == bad.resolve():
            raise PermissionError(13, "Permission denied", str(path))
        return original_open(path, mode, buffering, encoding, errors, newline)

    monkeypatch.setattr(Path, "open", guarded_open)
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(200, json={"message": {"role": "assistant", "content": "ok"}})
    )
    target = tmp_path / f"{surface}.json"
    argv = [good.name, bad.name] if surface == "reader" else ["map", "x", good.name, bad.name]

    code, out, _err = run_cli([*argv, "--manifest", str(target)], stdin="")

    assert code == 1
    assert len(out.splitlines()) == 1
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 2, "succeeded": 1, "skipped": 1, "failed": 1}


@pytest.mark.parametrize("surface", ("reader", "split"))
def test_multi_page_ocr_is_one_source_with_page_denominated_metering(
    surface: str,
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    (tmp_path / "book.pdf").write_bytes(minimal_pdf(["one", "two", "three"]))
    pages: list[dict[str, object]] = [
        {"index": index, "markdown": f"page {index + 1}", "images": [], "tables": []}
        for index in range(3)
    ]
    respx_mock.post(OCR).mock(return_value=httpx.Response(200, json={"pages": pages}))
    target = tmp_path / f"{surface}.json"
    argv = ["book.pdf"] if surface == "reader" else ["split", "book.pdf"]

    code, out, _err = run_cli([*argv, "--manifest", str(target)], stdin="")

    assert code == 0
    assert len(out.splitlines()) == 3
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}
    assert document["receipt"]["paid_conversions"] == 3


def test_multi_page_ocr_stays_one_source_after_per_page_model_work(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_BATCH", "off")
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    (tmp_path / "book.pdf").write_bytes(minimal_pdf(["one", "two", "three"]))
    pages: list[dict[str, object]] = [
        {"index": index, "markdown": f"page {index + 1}", "images": [], "tables": []}
        for index in range(3)
    ]
    respx_mock.post(OCR).mock(return_value=httpx.Response(200, json={"pages": pages}))
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "ok"}},
        )
    )
    target = tmp_path / "map.json"

    code, out, _err = run_cli(
        ["map", "summarize", "book.pdf", "--manifest", str(target)],
        stdin="",
    )

    assert code == 0
    assert out == "ok\nok\nok\n"
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}


def test_partially_processed_multi_page_source_keeps_usable_partial_exit(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_BATCH", "off")
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    (tmp_path / "book.pdf").write_bytes(minimal_pdf(["one", "two", "three"]))
    pages: list[dict[str, object]] = [
        {"index": index, "markdown": f"page {index + 1}", "images": [], "tables": []}
        for index in range(3)
    ]
    respx_mock.post(OCR).mock(return_value=httpx.Response(200, json={"pages": pages}))
    respx_mock.post(CHAT).side_effect = [
        httpx.Response(200, json={"message": {"role": "assistant", "content": "ok 1"}}),
        httpx.Response(400, json={"error": "bad row"}),
        httpx.Response(200, json={"message": {"role": "assistant", "content": "ok 3"}}),
    ]
    target = tmp_path / "partial-map.json"

    code, out, _err = run_cli(
        ["map", "summarize", "book.pdf", "--concurrency", "1", "--manifest", str(target)],
        stdin="",
    )

    assert code == 1
    assert out == "ok 1\nok 3\n"
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 1, "succeeded": 0, "skipped": 1, "failed": 1}


def test_multi_page_ocr_stays_one_source_after_batched_embeddings(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    (tmp_path / "book.pdf").write_bytes(minimal_pdf(["one", "two", "three"]))
    pages: list[dict[str, object]] = [
        {"index": index, "markdown": f"page {index + 1}", "images": [], "tables": []}
        for index in range(3)
    ]
    respx_mock.post(OCR).mock(return_value=httpx.Response(200, json={"pages": pages}))
    respx_mock.post(EMBED).mock(
        return_value=httpx.Response(
            200,
            json={"embeddings": [[1.0, 0.0], [0.0, 1.0], [1.0, 1.0]]},
        )
    )
    target = tmp_path / "embed.json"

    code, out, _err = run_cli(
        ["embed", "book.pdf", "--manifest", str(target)],
        stdin="",
    )

    assert code == 0
    assert len(out.splitlines()) == 3
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}


def test_multi_page_ocr_stays_one_source_in_whole_set_embedding_verb(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    (tmp_path / "book.pdf").write_bytes(minimal_pdf(["one", "two", "three"]))
    pages: list[dict[str, object]] = [
        {"index": index, "markdown": f"page {index + 1}", "images": [], "tables": []}
        for index in range(3)
    ]
    respx_mock.post(OCR).mock(return_value=httpx.Response(200, json={"pages": pages}))
    respx_mock.post(EMBED).mock(
        return_value=httpx.Response(
            200,
            json={"embeddings": [[1.0, 0.0], [0.9, 0.1], [0.8, 0.2]]},
        )
    )
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": '{"label":"pages"}'}},
        )
    )
    target = tmp_path / "cluster.json"

    code, out, _err = run_cli(
        ["cluster", "book.pdf", "--k", "1", "--manifest", str(target)],
        stdin="",
    )

    assert code == 0
    assert len(out.splitlines()) == 1
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}


def test_multi_page_ocr_stays_one_source_after_whole_set_reduction(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    (tmp_path / "book.pdf").write_bytes(minimal_pdf(["one", "two", "three"]))
    pages: list[dict[str, object]] = [
        {"index": index, "markdown": f"page {index + 1}", "images": [], "tables": []}
        for index in range(3)
    ]
    respx_mock.post(OCR).mock(return_value=httpx.Response(200, json={"pages": pages}))
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(
            200,
            json={"message": {"role": "assistant", "content": "summary"}},
        )
    )
    target = tmp_path / "reduce.json"

    code, out, _err = run_cli(
        ["reduce", "summarize", "book.pdf", "--manifest", str(target)],
        stdin="",
    )

    assert code == 0
    assert out == "summary\n"
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}
