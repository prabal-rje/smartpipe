"""Full-stack file-input tests: --in / --from-files through the real CLI and adapters."""

from __future__ import annotations

from typing import TYPE_CHECKING

import httpx
import pytest

from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path

    import respx

CHAT = "http://localhost:11434/api/chat"
EMBED = "http://localhost:11434/api/embed"


@pytest.fixture(autouse=True)
def local_models(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SEMPIPE_MODEL", "ollama/qwen3:8b")
    monkeypatch.setenv("SEMPIPE_EMBED_MODEL", "ollama/nomic-embed-text")


def test_map_reads_each_file_as_an_item(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    (tmp_path / "a.txt").write_text("alpha content")
    (tmp_path / "b.txt").write_text("beta content")
    respx_mock.post(CHAT).side_effect = [
        httpx.Response(200, json={"message": {"content": "A"}}),
        httpx.Response(200, json={"message": {"content": "B"}}),
    ]
    code, out, _err = run_cli(
        ["map", "First letter", "--in", str(tmp_path / "*.txt"), "--concurrency", "1"]
    )
    assert code == 0
    assert out == "A\nB\n"  # two files → two items, sorted


def test_filter_file_mode_emits_paths(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    keep = tmp_path / "keep.txt"
    keep.write_text("this document discusses a security incident")
    drop = tmp_path / "drop.txt"
    drop.write_text("this is a lunch menu")
    # files are glob-sorted: drop.txt before keep.txt → verdicts in that order
    respx_mock.post(CHAT).side_effect = [
        httpx.Response(200, json={"message": {"content": '{"match": false}'}}),  # drop.txt
        httpx.Response(200, json={"message": {"content": '{"match": true}'}}),  # keep.txt
    ]
    code, out, _err = run_cli(
        ["filter", "about security", "--in", str(tmp_path / "*.txt"), "--concurrency", "1"]
    )
    assert code == 0
    assert out == f"{keep}\n"  # the matching FILENAME, not the document text


def test_top_k_file_mode_ranks_filenames(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    (tmp_path / "close.txt").write_text("distributed systems and kubernetes")
    (tmp_path / "far.txt").write_text("baking bread at home")
    respx_mock.post(EMBED).side_effect = [
        httpx.Response(200, json={"embeddings": [[1.0, 0.0]]}),  # query
        # one chunked call: close.txt (glob-sorted first), then far.txt (DEFER-3)
        httpx.Response(200, json={"embeddings": [[1.0, 0.0], [0.0, 1.0]]}),
    ]
    glob = str(tmp_path / "*.txt")
    code, out, _err = run_cli(
        ["top_k", "1", "--near", "infra engineer", "--in", glob, "--concurrency", "1"]
    )
    assert code == 0
    assert out.splitlines()[0].startswith(f"{tmp_path / 'close.txt'}\t")


def test_from_files_reads_named_files(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    doc = tmp_path / "note.txt"
    doc.write_text("some content")
    respx_mock.post(CHAT).mock(
        return_value=httpx.Response(200, json={"message": {"content": "ok"}})
    )
    code, out, _err = run_cli(["map", "Summarize", "--from-files"], stdin=f"{doc}\n")
    assert code == 0
    assert out == "ok\n"


def test_empty_glob_is_usage_error(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    route = respx_mock.post(CHAT).mock(
        return_value=httpx.Response(200, json={"message": {"content": "x"}})
    )
    code, _out, err = run_cli(["map", "Summarize", "--in", str(tmp_path / "*.pdf")])
    assert code == 64
    assert "no files matched" in err
    assert route.call_count == 0


def test_map_describes_an_image_via_vision(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    import base64

    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\nPIXELS")
    route = respx_mock.post(CHAT).mock(
        return_value=httpx.Response(200, json={"message": {"content": "a red bicycle"}})
    )
    code, out, _err = run_cli(["map", "Describe", "--in", str(tmp_path / "*.png")])
    assert code == 0
    assert out == "a red bicycle\n"
    from sempipe.core.jsontools import as_items, as_record, as_str
    from tests.helpers.wire import sent_json

    body = as_record(sent_json(route))
    assert body is not None
    messages = as_items(body.get("messages"))
    assert messages
    user = as_record(messages[-1])
    images = as_items(user.get("images")) if user is not None else None
    first = as_str(images[0]) if images else None
    assert first is not None
    assert base64.b64decode(first) == b"\x89PNG\r\n\x1a\nPIXELS"
    system_message = as_record(messages[0])
    system = as_str(system_message.get("content")) if system_message is not None else None
    assert system is not None and system.startswith("The item is an image. ")  # pinned prefix


def test_filter_describes_image_items_via_the_local_model(
    run_cli: RunCli, respx_mock: respx.MockRouter, tmp_path: Path
) -> None:
    """D33: a LOCAL chat model converts images to text for free — the image is
    captioned, then judged, instead of skipped."""
    (tmp_path / "photo.png").write_bytes(b"\x89PNG\r\n\x1a\n" + b"\x00" * 4)
    (tmp_path / "note.txt").write_text("keep me")

    def answer(request: httpx.Request) -> httpx.Response:
        import json as jsonlib

        body = jsonlib.loads(request.content)
        has_images = any("images" in message for message in body["messages"])
        content = "a plain dark square" if has_images else '{"match": true}'
        return httpx.Response(200, json={"message": {"content": content}})

    respx_mock.post(CHAT).mock(side_effect=answer)
    code, out, err = run_cli(
        ["filter", "anything", "--in", str(tmp_path / "*"), "--concurrency", "1"]
    )
    assert code == 0  # both judged — the image entered as its description
    assert out == f"{tmp_path / 'note.txt'}\n{tmp_path / 'photo.png'}\n"
    assert "image → text (described by ollama/" in err  # the D33 row disclosure
