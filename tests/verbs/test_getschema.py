"""The getschema verb: fields, types, coverage — the first 30 seconds."""

from __future__ import annotations

import io
import json

from smartpipe.core.errors import ExitCode
from smartpipe.verbs.getschema import GetSchemaRequest, run_getschema


def _run(stdin_text: str, scan_all: bool = False) -> tuple[list[dict[str, object]], str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_getschema(
            GetSchemaRequest(scan_all=scan_all), stdin=io.StringIO(stdin_text), stdout=out
        )
    assert code is ExitCode.OK
    lines = out.getvalue().splitlines()
    rows = [json.loads(line) for line in lines if line.startswith("{")]
    return rows, err.getvalue()


NDJSON = (
    '{"id": 1, "sentiment": "neg", "score": 0.4}\n'
    '{"id": 2, "sentiment": "pos", "tags": ["a"]}\n'
    '{"id": "three", "sentiment": null}\n'
)


def test_types_union_and_coverage_count() -> None:
    rows, _err = _run(NDJSON)
    by_field = {row["field"]: row for row in rows}
    assert by_field["id"]["type"] == "integer|string"  # mixed types are the dirt worth seeing
    assert by_field["id"]["coverage"] == "100%"
    assert by_field["sentiment"]["coverage"] == "67%"  # null doesn't count as covered
    assert by_field["tags"]["type"] == "array"
    assert by_field["score"]["example"] == "0.4"


def test_fields_keep_first_seen_order() -> None:
    rows, _err = _run(NDJSON)
    assert [row["field"] for row in rows][:2] == ["id", "sentiment"]


def test_footer_suggests_the_best_covered_field() -> None:
    _rows, err = _run(NDJSON)
    assert "smartpipe chart id" in err


def test_plain_text_gets_a_one_line_answer() -> None:
    import contextlib

    out = io.StringIO()
    with contextlib.redirect_stderr(io.StringIO()):
        run_getschema(GetSchemaRequest(), stdin=io.StringIO("abc\nde\n"), stdout=out)
    assert "plain text lines (no fields) — 2 lines" in out.getvalue()


def test_long_examples_truncate() -> None:
    rows, _err = _run(json.dumps({"notes": "x" * 100}) + "\n")
    example = rows[0]["example"]
    assert isinstance(example, str) and len(example) <= 24 and example.endswith("…")


def test_boolean_is_not_integer() -> None:
    rows, _err = _run('{"ok": true}\n')
    assert rows[0]["type"] == "boolean"
