from __future__ import annotations

import io

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.io.writers import (
    OutputFormat,
    RenderMode,
    ResultWriter,
    WriterConfig,
    make_writer,
    resolve_format,
)


def _csv(fields: tuple[str, ...] | None = None) -> tuple[ResultWriter, io.StringIO]:
    out = io.StringIO()
    writer = make_writer(
        WriterConfig(mode=RenderMode.CSV, color=False, width=80, fields=fields), out
    )
    return writer, out


# --- CSV basics ---------------------------------------------------------------


def test_header_from_first_record_then_rows() -> None:
    writer, out = _csv()
    writer.write_record({"name": "Ada", "role": "eng"})
    writer.write_record({"name": "Bob", "role": "design"})
    assert out.getvalue() == "name,role\r\nAda,eng\r\nBob,design\r\n"  # RFC 4180 CRLF


def test_quoting_of_commas_and_quotes() -> None:
    writer, out = _csv()
    writer.write_record({"note": 'he said "hi", loudly'})
    assert out.getvalue() == 'note\r\n"he said ""hi"", loudly"\r\n'


def test_missing_key_is_empty_cell() -> None:
    writer, out = _csv()
    writer.write_record({"a": "1", "b": "2"})
    writer.write_record({"a": "3"})  # no b
    assert out.getvalue() == "a,b\r\n1,2\r\n3,\r\n"


def test_surprise_key_dropped_and_warned_once(capsys: pytest.CaptureFixture[str]) -> None:
    writer, out = _csv()
    writer.write_record({"a": "1"})
    writer.write_record({"a": "2", "c": "extra"})  # c wasn't in the header
    writer.write_record({"a": "3", "c": "again"})
    assert out.getvalue() == "a\r\n1\r\n2\r\n3\r\n"
    assert capsys.readouterr().err.count("column 'c' appeared") == 1  # warned once


def test_nested_values_become_compact_json() -> None:
    writer, out = _csv()
    writer.write_record({"tags": ["x", "y"], "meta": {"k": 1}})
    assert out.getvalue() == 'tags,meta\r\n"[""x"",""y""]","{""k"":1}"\r\n'


def test_scalar_rendering() -> None:
    writer, out = _csv()
    writer.write_record({"n": 42, "ok": True, "empty": None, "f": 1.5})
    assert out.getvalue() == "n,ok,empty,f\r\n42,true,,1.5\r\n"


def test_score_column_sorts_last() -> None:
    writer, out = _csv()
    writer.write_record({"__score": 0.9, "name": "Ada"})  # __score given first
    assert out.getvalue().splitlines()[0] == "name,__score"  # but header puts it last


def test_rank_and_distance_columns_sort_last() -> None:
    writer, out = _csv()
    writer.write_record({"__distance": 0.7, "__rank": 1, "text": "x"})
    assert out.getvalue().splitlines()[0] == "text,__rank,__distance"


def test_pre_migration_score_spellings_still_sort_last() -> None:
    # dual-read (item 76): pre-1.4 top_k wrote _score/_rank — one release of grace
    writer, out = _csv()
    writer.write_record({"_score": 0.9, "_rank": 1, "name": "Ada"})
    assert out.getvalue().splitlines()[0] == "name,_score,_rank"


def test_fields_pins_columns_and_order() -> None:
    writer, out = _csv(fields=("role", "name"))
    writer.write_record({"name": "Ada", "role": "eng", "extra": "x"})
    assert out.getvalue() == "role,name\r\neng,Ada\r\n"


def test_fields_missing_column_warned_once_and_kept_empty(
    capsys: pytest.CaptureFixture[str],
) -> None:
    writer, out = _csv(fields=("name", "email"))
    writer.write_record({"name": "Ada"})
    writer.write_record({"name": "Bob"})
    assert out.getvalue() == "name,email\r\nAda,\r\nBob,\r\n"
    assert capsys.readouterr().err.count("no field 'email' in the results") == 1


def test_explicit_fields_drop_extras_silently(capsys: pytest.CaptureFixture[str]) -> None:
    # dropping unrequested columns is the point of --fields — no surprise-key warning
    writer, out = _csv(fields=("name",))
    writer.write_record({"name": "Ada", "extra": "x"})
    assert out.getvalue() == "name\r\nAda\r\n"
    assert capsys.readouterr().err == ""


# --- TSV ----------------------------------------------------------------------


def test_tsv_delimiter_and_tab_stripping(capsys: pytest.CaptureFixture[str]) -> None:
    out = io.StringIO()
    writer = make_writer(WriterConfig(mode=RenderMode.TSV, color=False, width=80), out)
    writer.write_record({"a": "x\ty", "b": "line\nbreak"})
    assert out.getvalue() == "a\tb\r\nx y\tline break\r\n"  # tabs/newlines → spaces
    assert "replaced tabs/newlines" in capsys.readouterr().err


def test_write_text_stays_valid_csv() -> None:
    # a plain result shouldn't reach a CSV writer (guarded), but if it does, stay valid
    writer, out = _csv()
    writer.write_text("just a line")
    assert out.getvalue() == "result\r\njust a line\r\n"


# --- resolve_format guard -----------------------------------------------------


def test_csv_on_unstructured_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="needs structured output"):
        resolve_format(OutputFormat.CSV, {}, stdout_tty=False, structured=False)


def test_csv_on_structured_resolves() -> None:
    assert resolve_format(OutputFormat.CSV, {}, stdout_tty=True, structured=True) == RenderMode.CSV
    assert resolve_format(OutputFormat.TSV, {}, stdout_tty=False, structured=True) == RenderMode.TSV
