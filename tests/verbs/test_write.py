"""The write verb (wave 2, item 17): items → files, mirroring ingestion."""

from __future__ import annotations

import io
import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.verbs.write import WriteRequest, run_write

if TYPE_CHECKING:
    from pathlib import Path


async def _run(template: str, stdin_text: str, **kw: object) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_write(
        WriteRequest(template=template, **kw),  # type: ignore[arg-type]
        stdin=io.StringIO(stdin_text),
        stdout=out,
    )
    return code, out.getvalue()


def _sourced(text: str, path: str, line: int) -> str:
    return json.dumps({"text": text, "__source": {"path": path, "as": "lines", "line": line}})


async def test_line_cut_items_append_in_spine_order(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    # arrival order scrambled — the spine positions must win
    stdin_text = "\n".join([_sourced("second", "a.txt", 2), _sourced("first", "a.txt", 1)])
    code, out = await _run("out/{stem}.txt", stdin_text + "\n", field="text")
    assert code is ExitCode.OK
    assert (tmp_path / "out" / "a.txt").read_text() == "first\nsecond\n"
    assert out == "out/a.txt\n"  # the path written, so the pipe continues


async def test_records_write_jsonl_rows_with_spine_stripped(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    row = json.dumps({"lang": "fr", "text": "bonjour", "__custom_meta": 1})
    code, _out = await _run("all.jsonl", row + "\n")
    assert code is ExitCode.OK
    written = json.loads((tmp_path / "all.jsonl").read_text())
    assert written == {"lang": "fr", "text": "bonjour"}  # __ fields stripped


async def test_keep_meta_retains_the_spine(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    row = json.dumps({"a": 1, "__custom_meta": 2})
    await _run("all.jsonl", row + "\n", keep_meta=True)
    written = json.loads((tmp_path / "all.jsonl").read_text())
    assert written["__custom_meta"] == 2


async def test_content_fanout_groups_by_record_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    rows = [
        json.dumps({"lang": "fr", "text": "bonjour"}),
        json.dumps({"lang": "de", "text": "hallo"}),
        json.dumps({"lang": "fr", "text": "merci"}),
    ]
    code, out = await _run("by-lang/{lang}.jsonl", "\n".join(rows) + "\n")
    assert code is ExitCode.OK
    fr = [json.loads(line) for line in (tmp_path / "by-lang" / "fr.jsonl").read_text().splitlines()]
    assert [row["text"] for row in fr] == ["bonjour", "merci"]
    assert sorted(out.splitlines()) == ["by-lang/de.jsonl", "by-lang/fr.jsonl"]


async def test_media_items_write_their_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    monkeypatch.chdir(tmp_path)
    row = json.dumps(
        {
            "__media": {
                "kind": "image",
                "mime": "image/png",
                "data_b64": base64.b64encode(b"\x89PNGbytes").decode(),
            },
            "__source": {"path": "deck.pptx", "as": "file", "label": "deck.pptx img.1"},
        }
    )
    code, out = await _run("figs/{stem}-{index}.png", row + "\n")
    assert code is ExitCode.OK
    assert (tmp_path / "figs" / "deck-1.png").read_bytes() == b"\x89PNGbytes"
    assert out == "figs/deck-1.png\n"


async def test_single_file_collision_is_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import base64

    monkeypatch.chdir(tmp_path)
    payload = base64.b64encode(b"x").decode()
    row = json.dumps(
        {
            "__media": {"kind": "image", "mime": "image/png", "data_b64": payload},
            "__source": {"path": "deck.pptx", "as": "file"},
        }
    )
    with pytest.raises(UsageFault, match="written twice"):
        await _run("figs/{stem}.png", row + "\n" + row + "\n")


async def test_missing_template_field_is_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageFault, match="has no 'lang'"):
        await _run("by-{lang}.txt", json.dumps({"text": "x"}) + "\n")


async def test_plain_lines_append_raw_text(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, out = await _run("all.txt", "uno\ndos\n")
    assert code is ExitCode.OK
    assert (tmp_path / "all.txt").read_text() == "uno\ndos\n"
    assert out == "all.txt\n"


async def test_as_file_overrides_the_mirror(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    code, _out = await _run("rows/{index}.txt", "uno\ndos\n", as_mode="file")
    assert code is ExitCode.OK
    assert (tmp_path / "rows" / "1.txt").read_text() == "uno\n"
    assert (tmp_path / "rows" / "2.txt").read_text() == "dos\n"


async def test_field_missing_on_a_row_is_loud(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageFault, match="has no 'body'"):
        await _run("all.txt", json.dumps({"text": "x"}) + "\n", field="body")
