"""``--fields`` projection: identical selection + ordering in every structured mode.

Contract (plan/post-1.0/04-cli-ergonomics.md Task 1 + plan/ux.md):
projection and ordering apply the same way in ndjson / human / csv / tsv; a missing
key keeps the shape stable (null / empty cell / empty value); a field the results
don't carry is warned once on stderr, never an error.
"""

from __future__ import annotations

import io

import pytest

from smartpipe.cli.input_options import parse_fields
from smartpipe.core.errors import UsageFault
from smartpipe.io.writers import (
    OutputFormat,
    RenderMode,
    ResultWriter,
    WriterConfig,
    make_writer,
    resolve_format,
)

RECORD = {"a": 1, "b": 2, "c": 3}


def _writer(mode: RenderMode, fields: tuple[str, ...] | None) -> tuple[io.StringIO, ResultWriter]:
    stream = io.StringIO()
    writer = make_writer(WriterConfig(mode=mode, color=False, width=80, fields=fields), stream)
    return stream, writer


# --- selection + ordering, identical across modes ------------------------------


def test_ndjson_projects_and_orders() -> None:
    stream, writer = _writer(RenderMode.NDJSON, ("b", "a"))
    writer.write_record(RECORD)
    assert stream.getvalue() == '{"b":2,"a":1}\n'


def test_human_projects_and_orders() -> None:
    stream, writer = _writer(RenderMode.HUMAN, ("b", "a"))
    writer.write_record(RECORD)
    assert stream.getvalue() == "b: 2\na: 1\n\n"


def test_csv_projects_and_orders() -> None:
    stream, writer = _writer(RenderMode.CSV, ("b", "a"))
    writer.write_record(RECORD)
    assert stream.getvalue() == "b,a\r\n2,1\r\n"


def test_tsv_projects_and_orders() -> None:
    stream, writer = _writer(RenderMode.TSV, ("b", "a"))
    writer.write_record(RECORD)
    assert stream.getvalue() == "b\ta\r\n2\t1\r\n"


def test_text_writer_projects_records_too() -> None:
    # top_k's structured records flow through the TEXT writer (mixed text/record runs)
    stream, writer = _writer(RenderMode.TEXT, ("b", "a"))
    writer.write_record(RECORD)
    assert stream.getvalue() == '{"b":2,"a":1}\n'


def test_no_fields_means_no_projection() -> None:
    stream, writer = _writer(RenderMode.NDJSON, None)
    writer.write_record(RECORD)
    assert stream.getvalue() == '{"a":1,"b":2,"c":3}\n'


# --- missing key: shape stays stable --------------------------------------------


def test_missing_key_is_null_in_ndjson() -> None:
    stream, writer = _writer(RenderMode.NDJSON, ("b", "missing"))
    writer.write_record(RECORD)
    assert stream.getvalue() == '{"b":2,"missing":null}\n'


def test_missing_key_is_empty_cell_in_csv() -> None:
    stream, writer = _writer(RenderMode.CSV, ("b", "missing"))
    writer.write_record(RECORD)
    assert stream.getvalue() == "b,missing\r\n2,\r\n"


def test_missing_key_is_empty_value_in_human() -> None:
    stream, writer = _writer(RenderMode.HUMAN, ("b", "missing"))
    writer.write_record(RECORD)
    assert stream.getvalue() == "b: 2\nmissing: \n\n"


# --- unknown requested field: warned once, still emitted ------------------------


@pytest.mark.parametrize("mode", [RenderMode.NDJSON, RenderMode.HUMAN, RenderMode.CSV])
def test_unknown_field_warned_once_per_name(
    mode: RenderMode, capsys: pytest.CaptureFixture[str]
) -> None:
    _stream, writer = _writer(mode, ("a", "nope"))
    writer.write_record(RECORD)
    writer.write_record(RECORD)
    err = capsys.readouterr().err
    assert err.count("no field 'nope' in the results") == 1


def test_present_fields_never_warn(capsys: pytest.CaptureFixture[str]) -> None:
    _stream, writer = _writer(RenderMode.NDJSON, ("a", "b"))
    writer.write_record(RECORD)
    assert capsys.readouterr().err == ""


# --- the unstructured guard (resolve_format) ------------------------------------


def test_fields_on_unstructured_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="selects columns from structured output"):
        resolve_format(OutputFormat.AUTO, {}, stdout_tty=False, structured=False, fields=("a",))


def test_fields_on_structured_resolves() -> None:
    mode = resolve_format(OutputFormat.JSON, {}, stdout_tty=False, structured=True, fields=("a",))
    assert mode is RenderMode.NDJSON


# --- flag parsing ----------------------------------------------------------------


def test_parse_strips_whitespace_around_names() -> None:
    assert parse_fields(" a , b ") == ("a", "b")


def test_parse_empty_name_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="empty field name"):
        parse_fields("a,,b")


def test_parse_blank_value_is_usage_error() -> None:
    with pytest.raises(UsageFault, match="empty field name"):
        parse_fields("")


def test_parse_duplicate_names_usage_error_names_the_offender() -> None:
    with pytest.raises(UsageFault, match="names 'a' more than once"):
        parse_fields("a,b,a")
