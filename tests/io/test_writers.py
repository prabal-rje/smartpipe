from __future__ import annotations

import io

import pytest

from sempipe.core.errors import UsageFault
from sempipe.io.items import item_from_line
from sempipe.io.writers import (
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
        (OutputFormat.AUTO, {"SEMPIPE_OUTPUT": "json"}, True, True, RenderMode.NDJSON),
        (OutputFormat.TEXT, {"SEMPIPE_OUTPUT": "json"}, False, True, RenderMode.TEXT),  # flag wins
        (OutputFormat.AUTO, {"SEMPIPE_OUTPUT": ""}, False, True, RenderMode.NDJSON),  # empty=unset
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
            OutputFormat.AUTO, {"SEMPIPE_OUTPUT": "yaml"}, stdout_tty=False, structured=False
        )
    assert "SEMPIPE_OUTPUT" in str(excinfo.value)


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


def test_human_writer_renders_non_string_values_as_compact_json() -> None:
    stream, writer = _writer(RenderMode.HUMAN)
    writer.write_record({"ok": True, "tags": ["a", "b"]})
    assert stream.getvalue() == 'ok: true\ntags: ["a","b"]\n\n'


def test_human_writer_truncates_long_values_to_width() -> None:
    stream, writer = _writer(RenderMode.HUMAN, width=20)
    writer.write_record({"summary": "x" * 30})
    line = stream.getvalue().splitlines()[0]
    assert len(line) == 20
    assert line == "summary: " + "x" * 10 + "…"


def test_human_writer_truncates_wide_chars_by_cells_never_overshooting() -> None:
    # DEFER-2: a Wide (CJK) value at the boundary must not overshoot the terminal
    from sempipe.io.text import display_width

    stream, writer = _writer(RenderMode.HUMAN, width=20)
    writer.write_record({"summary": "名" * 30})
    line = stream.getvalue().splitlines()[0]
    assert display_width(line) <= 20
    assert line.endswith("…")


def test_human_writer_dims_keys_when_color_on() -> None:
    stream, writer = _writer(RenderMode.HUMAN, color=True)
    writer.write_record({"a": "b"})
    assert stream.getvalue() == "\x1b[2ma:\x1b[0m b\n\n"


def test_human_writer_plain_text_is_unstyled() -> None:
    stream, writer = _writer(RenderMode.HUMAN, color=True)
    writer.write_text("result line")
    assert stream.getvalue() == "result line\n"
