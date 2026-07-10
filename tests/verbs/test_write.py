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


async def test_text_only_records_mirror_back_as_plain_text(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # law 5 at the write edge: reader lines round-trip byte-identically
    monkeypatch.chdir(tmp_path)
    rows = [
        json.dumps({"text": "first", "__source": {"path": "notes.txt", "as": "lines", "line": 1}}),
        json.dumps({"text": "second", "__source": {"path": "notes.txt", "as": "lines", "line": 2}}),
    ]
    code, _out = await _run("out/{name}", "\n".join(rows) + "\n")
    assert code is ExitCode.OK
    assert (tmp_path / "out" / "notes.txt").read_text() == "first\nsecond\n"


async def test_keep_meta_forces_jsonl_even_for_text_only_records(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    row = json.dumps({"text": "kept", "__source": {"path": "n.txt", "as": "lines", "line": 1}})
    await _run("out/{name}", row + "\n", keep_meta=True)
    written = json.loads((tmp_path / "out" / "n.txt").read_text())
    assert written["__source"]["line"] == 1  # meta can't ride plain text


# --- template vars take field paths (item 63) --------------------------------------


async def test_template_var_takes_a_path(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    rows = [
        json.dumps({"user": {"plan": "pro"}, "text": "a"}),
        json.dumps({"user": {"plan": "free"}, "text": "b"}),
    ]
    code, out = await _run("by-plan/{user.plan}.jsonl", "\n".join(rows) + "\n")
    assert code is ExitCode.OK
    assert (tmp_path / "by-plan" / "pro.jsonl").exists()
    assert (tmp_path / "by-plan" / "free.jsonl").exists()
    assert sorted(out.splitlines()) == ["by-plan/free.jsonl", "by-plan/pro.jsonl"]


async def test_template_var_takes_an_index_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    row = json.dumps({"items": [{"sku": "A1"}, {"sku": "B2"}], "text": "x"})
    code, _out = await _run("sku/{items[0].sku}.jsonl", row + "\n")
    assert code is ExitCode.OK
    assert (tmp_path / "sku" / "A1.jsonl").exists()


async def test_exact_flat_dotted_key_wins_over_traversal(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # THE COMPAT RULE: a literal column named "user.plan" beats the nested path
    monkeypatch.chdir(tmp_path)
    row = json.dumps({"user.plan": "flat", "user": {"plan": "nested"}, "text": "x"})
    code, _out = await _run("{user.plan}.jsonl", row + "\n")
    assert code is ExitCode.OK
    assert (tmp_path / "flat.jsonl").exists()


async def test_reserved_word_wins_over_a_same_named_field(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    # {name} is ALWAYS the origin's basename, even when the record has a "name"
    monkeypatch.chdir(tmp_path)
    row = json.dumps(
        {"name": "field-value", "__source": {"path": "notes.txt", "as": "lines", "line": 1}}
    )
    code, _out = await _run("out/{name}", row + "\n")
    assert code is ExitCode.OK
    assert (tmp_path / "out" / "notes.txt").exists()


async def test_missing_template_path_is_loud_and_verbatim(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageFault, match=r"has no 'user\.zip'"):
        await _run("by-{user.zip}.txt", json.dumps({"user": {"plan": "pro"}}) + "\n")


async def test_malformed_template_path_faults_before_any_file(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageFault, match=r"a\.b\[x\] - index must be a number"):
        await _run("out/{a.b[x]}.txt", json.dumps({"text": "x"}) + "\n")
    assert list(tmp_path.iterdir()) == []  # loud BEFORE the first write


async def test_positional_template_var_is_a_usage_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageFault, match="template vars are named"):
        await _run("out/{}.txt", "line\n")


async def test_unbalanced_template_brace_is_a_usage_fault(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    with pytest.raises(UsageFault, match="bad template"):
        await _run("out/{stem.txt", "line\n")


async def test_format_specs_keep_working(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.chdir(tmp_path)
    code, _out = await _run("rows/{index:03d}.txt", "uno\n", as_mode="file")
    assert code is ExitCode.OK
    assert (tmp_path / "rows" / "001.txt").read_text() == "uno\n"
