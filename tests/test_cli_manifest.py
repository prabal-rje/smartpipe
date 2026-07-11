"""Full-stack ``--manifest`` tests (item 65a): real CLI, mocked HTTP only.

The manifest is the citable methods-section artifact - these tests pin that
the file lands on every exit path that produced results, records the resolved
models/prompt/schema/counts/receipt, and never rides stdout.
"""

from __future__ import annotations

import hashlib
import json
from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli
from tests.helpers.pdf import minimal_pdf

if TYPE_CHECKING:
    from pathlib import Path

    import respx

CHAT = "http://localhost:11434/api/chat"
EMBED = "http://localhost:11434/api/embed"
OCR = "https://api.mistral.ai/v1/ocr"
PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


@pytest.fixture(autouse=True)
def local_model(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.delenv("SMARTPIPE_OUTPUT", raising=False)


def _reply(content: str) -> httpx.Response:
    return httpx.Response(200, json={"message": {"role": "assistant", "content": content}})


def _embeddings(vector: list[float]) -> httpx.Response:
    return httpx.Response(200, json={"embeddings": [vector]})


def _ocr_page(markdown: str) -> dict[str, object]:
    return {"index": 0, "markdown": markdown, "images": [], "tables": []}


def test_map_manifest_records_the_run(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("hola"))
    target = tmp_path / "run.json"
    code, out, err = run_cli(
        ["map", "translate to Spanish", "--manifest", str(target)], stdin="hello\n"
    )
    assert code == 0
    assert out == "hola\n"  # stdout stays sacred - results only
    assert f"manifest: {target}" in err
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["verb"] == "map"
    assert document["argv"] == ["map", "translate to Spanish", "--manifest", str(target)]
    assert document["models"] == {"chat": "ollama/qwen3:8b"}
    assert document["prompt"] == {
        "text": "translate to Spanish",
        "sha256": hashlib.sha256(b"translate to Spanish").hexdigest(),
    }
    assert document["schema"] is None
    assert document["determinism"] == {"temperature": 0.0}
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}
    assert document["run"]["exit_code"] == 0
    assert document["run"]["exit_status"] == "ok"


def test_partial_run_manifest_keeps_the_honest_counts(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).side_effect = [
        _reply('{"v": "one"}'),
        _reply("not json"),
        _reply("still not json"),
        _reply('{"v": "three"}'),
    ]
    target = tmp_path / "run.json"
    code, _out, _err = run_cli(
        ["map", "Extract {v}", "--concurrency", "1", "--manifest", str(target)],
        stdin="a\nb\nc\n",
    )
    assert code == 1
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 3, "succeeded": 2, "skipped": 1, "failed": 1}
    assert document["run"]["exit_status"] == "partial"
    assert document["schema"] is not None  # braces compiled to a schema - recorded


def test_belted_run_manifest_says_partial(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("ok"))
    target = tmp_path / "run.json"
    code, _out, _err = run_cli(
        ["map", "x", "--max-calls", "1", "--concurrency", "1", "--manifest", str(target)],
        stdin="a\nb\nc\n",
    )
    assert code == 1  # a capped run never exits 0 (D18)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["run"]["exit_status"] == "partial"


def test_empty_filter_manifest_records_zero_source_items(run_cli: RunCli, tmp_path: Path) -> None:
    target = tmp_path / "empty-filter.json"

    code, out, _err = run_cli(
        ["filter", "is urgent", "--manifest", str(target)],
        stdin="",
    )

    assert code == 0
    assert out == ""
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 0, "succeeded": 0, "skipped": 0, "failed": 0}


def test_filter_policy_exclusion_is_skipped_but_not_failed(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply('{"match": true}'))
    target = tmp_path / "filter-exclusion.json"

    code, out, _err = run_cli(
        ["filter", "{priority} is urgent", "--manifest", str(target)],
        stdin='{"priority": "high"}\n{"other": true}\n',
    )

    assert code == 1
    assert out == '{"priority": "high"}\n'
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 2, "succeeded": 1, "skipped": 1, "failed": 0}


def test_map_dry_run_manifest_counts_the_previewed_source_without_a_model(
    run_cli: RunCli, tmp_path: Path
) -> None:
    target = tmp_path / "dry-run.json"

    code, out, _err = run_cli(
        ["map", "summarize", "--dry-run", "--manifest", str(target)],
        stdin="one row\nsecond row\n",
    )

    assert code == 0
    assert "--- user ---" in out
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["models"] == {}
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}


def test_embed_manifest_records_the_embed_role(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")
    respx_mock.post(EMBED).mock(return_value=_embeddings([1.0, 0.0]))
    target = tmp_path / "run.json"
    code, _out, _err = run_cli(["embed", "--manifest", str(target)], stdin="a\n")
    assert code == 0
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["verb"] == "embed"
    assert document["models"]["embed"] == "ollama/nomic-embed-text"
    # embed also RESOLVES a chat model for the conversion ladder (captions);
    # the manifest records resolutions, so the role appears here too
    assert document["models"]["chat"] == "ollama/qwen3:8b"
    assert document["prompt"] is None
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}


def test_embed_partial_manifest_distinguishes_failed_items(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")
    respx_mock.post(EMBED).side_effect = [
        _embeddings([1.0, 0.0]),
        httpx.Response(200, json={"embeddings": [["bad"]]}),
    ]
    target = tmp_path / "embed-partial.json"

    code, _out, _err = run_cli(
        ["embed", "--concurrency", "1", "--manifest", str(target)],
        stdin="good\nbroken\n",
    )

    assert code == 1
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 2, "succeeded": 1, "skipped": 1, "failed": 1}


def test_top_k_partial_manifest_distinguishes_failed_items(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")
    respx_mock.post(EMBED).side_effect = [
        _embeddings([1.0, 0.0]),
        httpx.Response(200, json={"embeddings": [[1.0, 0.0], ["bad"]]}),
        _embeddings([1.0, 0.0]),
        httpx.Response(200, json={"embeddings": [["bad"]]}),
    ]
    target = tmp_path / "top-k-partial.json"

    code, _out, _err = run_cli(
        ["top_k", "5", "--near", "q", "--manifest", str(target)],
        stdin="good\nbroken\n",
    )

    assert code == 1
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 2, "succeeded": 1, "skipped": 1, "failed": 1}


def test_missing_manifest_directory_faults_before_any_spend(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    route = respx_mock.post(CHAT).mock(return_value=_reply("never"))
    code, out, err = run_cli(
        ["map", "x", "--manifest", str(tmp_path / "missing-dir" / "run.json")], stdin="a\n"
    )
    assert code == 64
    assert out == ""
    assert "does not exist" in err
    assert route.call_count == 0  # the fault landed before any model call


def test_setup_fault_leaves_no_manifest(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # no model configured, no ollama listening: exit 2 before any results -
    # there was no run to record
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # nothing listens on port 9
    target = tmp_path / "run.json"
    code, _out, _err = run_cli(["map", "x", "--manifest", str(target)], stdin="a\n")
    assert code == 2
    assert not target.exists()


def test_a_second_run_overwrites_the_manifest(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    respx_mock.post(CHAT).mock(return_value=_reply("first"))
    target = tmp_path / "run.json"
    assert run_cli(["map", "one", "--manifest", str(target)], stdin="a\n")[0] == 0
    assert run_cli(["map", "two", "--manifest", str(target)], stdin="a\nb\n")[0] == 0
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["prompt"]["text"] == "two"  # a record of THIS run
    assert document["items"]["in"] == 2


@pytest.mark.parametrize(("surface", "verb"), (("reader", "read"), ("split", "split")))
def test_ocr_surfaces_write_atomic_manifests_without_touching_stdout(
    surface: str,
    verb: str,
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    (tmp_path / "scan.png").write_bytes(PNG)
    target = tmp_path / f"{surface}.json"
    target.write_text("old, incomplete content", encoding="utf-8")
    respx_mock.post(OCR).mock(
        return_value=httpx.Response(200, json={"pages": [_ocr_page("SCANNED TEXT")]})
    )
    argv = ["scan.png"] if surface == "reader" else ["split", "scan.png"]
    code, out, err = run_cli([*argv, "--manifest", str(target)], stdin="")

    assert code == 0
    assert len(out.splitlines()) == 1
    assert "manifest" not in out  # sacred stdout remains the one result row
    assert json.loads(out)["text"] == "SCANNED TEXT"
    assert f"manifest: {target}" in err
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["verb"] == verb
    assert document["models"] == {"ocr": "mistral/mistral-ocr-latest"}
    assert document["receipt"]["paid_conversions"] == 1
    assert document["items"] == {"in": 1, "succeeded": 1, "skipped": 0, "failed": 0}
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("surface", ("reader", "split"))
def test_ocr_surface_manifest_destination_faults_before_parser_spend(
    surface: str,
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.delenv("MISTRAL_API_KEY", raising=False)
    (tmp_path / "scan.png").write_bytes(PNG)
    route = respx_mock.post(OCR).mock(
        return_value=httpx.Response(200, json={"pages": [_ocr_page("never")]})
    )
    target = tmp_path / "missing" / "run.json"
    argv = ["scan.png"] if surface == "reader" else ["split", "scan.png"]
    code, out, err = run_cli([*argv, "--manifest", str(target)], stdin="")

    assert code == 64
    assert out == ""
    assert "does not exist" in err
    assert route.call_count == 0
    assert not target.exists()


@pytest.mark.parametrize("surface", ("reader", "split"))
def test_manifest_target_cannot_alias_a_named_input(
    surface: str,
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    if surface == "reader":
        source = tmp_path / "scan.png"
        source.write_bytes(PNG)
        argv = [source.name]
    else:
        source = tmp_path / "scan.pdf"
        source.write_bytes(minimal_pdf(["source text"]))
        argv = ["split", "--by", "pages", source.name]
    original = source.read_bytes()
    route = respx_mock.post(OCR).mock(
        return_value=httpx.Response(200, json={"pages": [_ocr_page("never")]})
    )

    code, out, err = run_cli([*argv, "--manifest", str(source)], stdin="")

    assert code == 64
    assert out == ""
    assert "aliases input" in err
    assert route.call_count == 0
    assert source.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


@pytest.mark.parametrize("surface", ("join", "diff"))
def test_manifest_target_cannot_alias_a_right_input(
    surface: str,
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")
    source = tmp_path / "right.png"
    source.write_bytes(PNG)
    original = source.read_bytes()
    routes = (
        respx_mock.post(OCR).mock(
            return_value=httpx.Response(200, json={"pages": [_ocr_page("never")]})
        ),
        respx_mock.post(CHAT).mock(return_value=_reply("never")),
        respx_mock.post(EMBED).mock(return_value=_embeddings([1.0, 0.0])),
    )
    argv = (
        ["join", "x {left.text} y {right.text}", "--right", str(source)]
        if surface == "join"
        else ["diff", "--right", str(source)]
    )

    code, out, err = run_cli([*argv, "--manifest", str(source)], stdin="left\n")

    assert code == 64
    assert out == ""
    assert "aliases --right input" in err
    assert all(route.call_count == 0 for route in routes)
    assert source.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_streamed_filename_cannot_alias_the_manifest_target(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
) -> None:
    source = tmp_path / "input.txt"
    source.write_bytes(b"irreplaceable input\n")
    original = source.read_bytes()
    route = respx_mock.post(CHAT).mock(return_value=_reply("never"))

    code, out, err = run_cli(
        ["map", "x", "--from-files", "--manifest", str(source)],
        stdin=f"{source}\n",
    )

    assert code == 64
    assert out == ""
    assert "aliases input" in err
    assert route.call_count == 0
    assert source.read_bytes() == original
    assert list(tmp_path.glob("*.tmp")) == []


def test_join_unmatched_output_cannot_alias_the_manifest_target(
    run_cli: RunCli,
    respx_mock: respx.MockRouter,
    tmp_path: Path,
) -> None:
    right = tmp_path / "right.jsonl"
    right.write_text('{"id": 1}\n', encoding="utf-8")
    target = tmp_path / "collision.json"
    routes = (
        respx_mock.post(CHAT).mock(return_value=_reply("never")),
        respx_mock.post(EMBED).mock(return_value=_embeddings([1.0, 0.0])),
    )

    code, out, err = run_cli(
        [
            "join",
            "--on",
            "left.id == right.id",
            "--right",
            str(right),
            "--unmatched",
            str(target),
            "--manifest",
            str(target),
        ],
        stdin='{"id": 2}\n',
    )

    assert code == 64
    assert out == ""
    assert "aliases --unmatched output" in err
    assert all(route.call_count == 0 for route in routes)
    assert right.read_text(encoding="utf-8") == '{"id": 1}\n'
    assert not target.exists()
    assert list(tmp_path.glob("*.tmp")) == []


def test_split_help_exposes_manifest(run_cli: RunCli) -> None:
    code, out, _err = run_cli(["split", "--help"], stdin="")
    assert code == 0
    assert "--manifest PATH" in out
    assert "dedicated OCR pages" in out
