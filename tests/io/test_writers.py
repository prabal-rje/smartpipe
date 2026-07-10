from __future__ import annotations

import io
from collections.abc import Mapping

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.io.items import item_from_line
from smartpipe.io.writers import (
    OutputFormat,
    RenderMode,
    ResultWriter,
    WriterConfig,
    make_writer,
    resolve_format,
)

# --- format resolution -------------------------------------------------------


@pytest.mark.parametrize(
    ("flag", "env", "tty", "structured", "expected"),
    [
        (OutputFormat.AUTO, {}, True, True, RenderMode.HUMAN),
        (OutputFormat.AUTO, {}, False, True, RenderMode.NDJSON),
        (OutputFormat.AUTO, {}, True, False, RenderMode.TEXT),
        (OutputFormat.AUTO, {}, False, False, RenderMode.TEXT),
        (OutputFormat.JSON, {}, True, False, RenderMode.NDJSON),  # forced JSON even on a TTY
        (OutputFormat.TEXT, {}, False, True, RenderMode.TEXT),  # forced text even when piping
        (OutputFormat.AUTO, {"SMARTPIPE_OUTPUT": "json"}, True, True, RenderMode.NDJSON),
        # the flag wins over the env
        (OutputFormat.TEXT, {"SMARTPIPE_OUTPUT": "json"}, False, True, RenderMode.TEXT),
        # empty env value reads as unset
        (OutputFormat.AUTO, {"SMARTPIPE_OUTPUT": ""}, False, True, RenderMode.NDJSON),
    ],
)
def test_resolve_format(
    flag: OutputFormat,
    env: dict[str, str],
    tty: bool,
    structured: bool,
    expected: RenderMode,
) -> None:
    assert resolve_format(flag, env, stdout_tty=tty, structured=structured) is expected


def test_invalid_env_value_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault) as excinfo:
        resolve_format(
            OutputFormat.AUTO, {"SMARTPIPE_OUTPUT": "yaml"}, stdout_tty=False, structured=False
        )
    assert "SMARTPIPE_OUTPUT" in str(excinfo.value)


def test_csv_and_tsv_resolve_when_structured() -> None:
    # detailed CSV/TSV behavior lives in tests/io/test_table_writer.py
    assert resolve_format(OutputFormat.CSV, {}, stdout_tty=False, structured=True) == RenderMode.CSV
    assert resolve_format(OutputFormat.TSV, {}, stdout_tty=True, structured=True) == RenderMode.TSV


def test_flush_is_safe_on_every_writer() -> None:
    for mode in (RenderMode.TEXT, RenderMode.NDJSON, RenderMode.HUMAN):
        _stream, writer = _writer(mode)
        writer.flush()


# --- writers ------------------------------------------------------------------


def _writer(
    mode: RenderMode, *, color: bool = False, width: int = 80
) -> tuple[io.StringIO, ResultWriter]:
    stream = io.StringIO()
    writer = make_writer(WriterConfig(mode=mode, color=color, width=width), stream)
    return stream, writer


def test_ndjson_records_are_compact_unescaped_and_flushed_per_line() -> None:
    stream, writer = _writer(RenderMode.NDJSON)
    writer.write_record({"a": 1, "café": "sí"})
    assert stream.getvalue() == '{"a":1,"café":"sí"}\n'


def test_ndjson_wraps_plain_text_as_result() -> None:
    stream, writer = _writer(RenderMode.NDJSON)
    writer.write_text("hola mundo")
    assert stream.getvalue() == '{"result":"hola mundo"}\n'


def test_passthrough_is_byte_faithful_in_every_mode() -> None:
    quirky = '{ "a" :1}'
    for mode in (RenderMode.TEXT, RenderMode.NDJSON, RenderMode.HUMAN):
        stream, writer = _writer(mode)
        writer.write_passthrough(item_from_line(quirky + "\n", 0))
        assert stream.getvalue() == quirky + "\n", mode


def test_text_writer_emits_lines_and_compact_json_for_records() -> None:
    stream, writer = _writer(RenderMode.TEXT)
    writer.write_text("hola")
    writer.write_record({"a": 1})
    assert stream.getvalue() == 'hola\n{"a":1}\n'


def test_human_writer_renders_key_value_lines_with_blank_separator() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record({"vendor": "Acme", "total": 1250.0})
    assert stream.getvalue() == "vendor: Acme\ntotal: 1250.0\n\n"


def test_human_writer_renders_yaml_ish_blocks() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record({"ok": True, "tags": ["a", "b"], "who": {"name": "Ada"}})
    assert stream.getvalue() == ("ok: true\ntags:\n  - a\n  - b\nwho:\n  name: Ada\n\n")


def test_human_writer_truncates_long_strings_with_a_count() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record({"summary": "x" * 450})
    line = stream.getvalue().splitlines()[0]
    assert line == "summary: " + "x" * 400 + "… (+50 chars)"


def test_human_writer_full_disables_truncation() -> None:
    stream = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.HUMAN, color=False, width=80, full=True), stream
    )
    writer.write_record({"summary": "x" * 450, "items": list(range(20))})
    text = stream.getvalue()
    assert "x" * 450 in text
    assert "(+" not in text  # nothing hidden under --full


def test_human_writer_caps_long_lists_with_a_count() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record({"items": list(range(14))})
    text = stream.getvalue()
    assert "  - 9" in text and "  - 10" not in text
    assert "… (+4 items)" in text


def test_human_writer_spine_renders_at_the_bottom_and_media_never_dumps_base64() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record(
        {
            "__media": {"kind": "image", "mime": "image/png", "data_b64": "A" * 65536},
            "result": "a chart",
            "__source": {"path": "deck.pptx", "as": "file"},
        }
    )
    lines = stream.getvalue().splitlines()
    assert lines[0] == "result: a chart"  # payload first, spine at the bottom
    assert any(line.startswith("__media: image/png (48 KB)") for line in lines)
    assert "A" * 100 not in stream.getvalue()  # never the base64


def test_human_writer_multiline_strings_render_as_block_scalars() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record({"body": "first\nsecond"})
    assert stream.getvalue() == "body: |\n  first\n  second\n\n"


def test_human_writer_dims_keys_when_color_on() -> None:
    stream, writer = _writer(RenderMode.HUMAN, color=True)
    writer.write_record({"a": "b"})
    assert stream.getvalue() == "\x1b[36m#1\x1b[0m\n\x1b[2ma:\x1b[0m b\n\n"


def test_human_writer_plain_text_is_unstyled() -> None:
    stream, writer = _writer(RenderMode.HUMAN, color=True)
    writer.write_text("result line")
    assert stream.getvalue() == "result line\n"


# --- media previews: the injected hook (item: terminal media previews) -----------


_MEDIA_ROW: dict[str, object] = {
    "result": "a chart",
    "__media": {"kind": "image", "mime": "image/png", "data_b64": "A" * 64},
    "__source": {"path": "deck.pptx", "as": "file"},
}


def test_human_writer_renders_preview_lines_under_the_media_summary() -> None:
    seen: list[Mapping[str, object]] = []

    def fake_preview(record: Mapping[str, object]) -> list[str]:
        seen.append(record)
        return ["  [thumbnail]"]

    stream = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.HUMAN, color=False, width=80, media_lines=fake_preview),
        stream,
    )
    writer.write_record(_MEDIA_ROW)
    lines = stream.getvalue().splitlines()
    media_at = next(index for index, line in enumerate(lines) if line.startswith("__media:"))
    assert lines[media_at + 1] == "  [thumbnail]"  # directly under the summary line
    assert lines[media_at + 2].startswith("__source:")  # the spine continues below
    assert seen == [_MEDIA_ROW]  # the hook sees the whole record (it needs __source)


def test_preview_hook_is_never_called_without_media() -> None:
    def explode(record: Mapping[str, object]) -> list[str]:
        raise AssertionError("no __media, no preview call")

    stream = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.HUMAN, color=False, width=80, media_lines=explode), stream
    )
    writer.write_record({"result": "plain"})
    assert stream.getvalue() == "result: plain\n\n"


def test_ndjson_pipes_stay_byte_identical_with_the_hook_wired() -> None:
    # stdout is sacred: piped record output never grows preview bytes
    def explode(record: Mapping[str, object]) -> list[str]:
        raise AssertionError("NDJSON must never render previews")

    stream = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, media_lines=explode), stream
    )
    writer.write_record(_MEDIA_ROW)
    assert stream.getvalue() == (
        '{"result":"a chart",'
        '"__media":{"kind":"image","mime":"image/png","data_b64":"' + "A" * 64 + '"},'
        '"__source":{"path":"deck.pptx","as":"file"}}\n'
    )


def test_human_writer_without_a_hook_is_byte_identical_to_today() -> None:
    # NO_COLOR / kill-switch path: media_lines stays None and nothing changes
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record(_MEDIA_ROW)
    assert stream.getvalue() == (
        "result: a chart\n__media: image/png (48 B)\n__source:\n  path: deck.pptx\n  as: file\n\n"
    )


# --- --keep-invalid rows --------------------------------------------------------


def test_human_writer_renders_invalid_rows_as_one_dim_compact_line() -> None:
    stream, writer = _writer(RenderMode.HUMAN, color=True)
    writer.write_record({"__invalid": True, "__error": "'v' is required", "__raw": "x" * 100})
    body = [line for line in stream.getvalue().splitlines() if line]
    assert len(body) == 1  # never a key/value block
    line = body[0]
    assert line.startswith("\x1b[2m✗ invalid: 'v' is required · ")
    assert line.endswith("…\x1b[0m")
    assert "x" * 70 in line  # first ~70 chars of the raw reply survive
    assert "x" * 71 not in line


def test_human_writer_invalid_line_is_plain_without_color() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record({"__invalid": True, "__error": "boom", "__raw": "short reply"})
    assert stream.getvalue() == "✗ invalid: boom · short reply\n\n"


def test_human_writer_invalid_raw_flattens_to_one_line() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record({"__invalid": True, "__error": "boom", "__raw": "a\nb\tc"})
    assert stream.getvalue() == "✗ invalid: boom · a b c\n\n"


def test_ndjson_invalid_rows_bypass_fields_projection() -> None:
    # piped output stays the full machine-readable failure row, even under --fields
    stream = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, fields=("v",)), stream
    )
    writer.write_record({"__invalid": True, "__error": "e", "__raw": "r"})
    assert stream.getvalue() == '{"__invalid":true,"__error":"e","__raw":"r"}\n'


# --- --bare: strip the __ spine (wave 2, item 18) ---------------------------------


def test_bare_strips_meta_from_jsonl_records() -> None:
    stream = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, bare=True), stream
    )
    writer.write_record(
        {
            "result": "hola",
            "__source": {"path": "-", "as": "lines", "line": 1},
            "__sources": [{"path": "-", "as": "lines", "line": 1}],  # item 64 rides the same rule
        }
    )
    assert stream.getvalue() == '{"result":"hola"}\n'


def test_bare_strips_the_ranking_stamps() -> None:
    # item 76: __score/__rank/__distance live in the __ namespace — --bare drops them
    stream = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, bare=True), stream
    )
    writer.write_record({"text": "hit", "__score": 0.91, "__rank": 1, "__distance": 0.7})
    assert stream.getvalue() == '{"text":"hit"}\n'


def test_bare_never_guts_an_invalid_marker_row() -> None:
    stream = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.NDJSON, color=False, width=80, bare=True), stream
    )
    writer.write_record({"__invalid": True, "__error": "e", "__raw": "r"})
    assert stream.getvalue() == '{"__invalid":true,"__error":"e","__raw":"r"}\n'


def test_text_writer_warns_once_about_multiline_results(
    capsys: pytest.CaptureFixture[str],
) -> None:
    # item 20: plain-TEXT output with internal newlines to a NON-TTY stdout
    _stream, writer = _writer(RenderMode.TEXT)
    writer.write_text("two\nlines")
    writer.write_text("more\nlines")
    err = capsys.readouterr().err
    assert err.count("--output json") == 1  # once, naming the fix


def test_human_blocks_gain_a_cyan_ordinal_at_the_tty() -> None:
    """'Look at object 5' needs a handle: each block gets #N when color is
    on (the human view); piped/NO_COLOR output stays byte-identical."""
    out = io.StringIO()
    writer = make_writer(WriterConfig(mode=RenderMode.HUMAN, color=True, width=80), out)
    writer.write_record({"a": 1})
    writer.write_record({"a": 2})
    writer.flush()
    assert "\x1b[36m#1\x1b[0m\n" in out.getvalue()
    assert "\x1b[36m#2\x1b[0m\n" in out.getvalue()


def test_human_blocks_carry_no_ordinal_without_color() -> None:
    out = io.StringIO()
    writer = make_writer(WriterConfig(mode=RenderMode.HUMAN, color=False, width=80), out)
    writer.write_record({"a": 1})
    writer.flush()
    assert "#1" not in out.getvalue()
