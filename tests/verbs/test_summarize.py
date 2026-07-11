"""The summarize verb: one pass, ranked groups, disclosed skips."""

from __future__ import annotations

import io
import json

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.verbs.summarize import SummarizeRequest, run_summarize


def _run(expression: str, stdin_text: str) -> tuple[ExitCode, list[dict[str, object]], str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_summarize(
            SummarizeRequest(expression), stdin=io.StringIO(stdin_text), stdout=out
        )
    return code, [json.loads(line) for line in out.getvalue().splitlines()], err.getvalue()


NDJSON = (
    '{"region": "EU", "total": 50}\n'
    '{"region": "EU", "total": 100}\n'
    '{"region": "EU", "total": "n/a"}\n'
    '{"region": "US", "total": 10}\n'
)


def test_groups_rank_largest_first_with_kql_names() -> None:
    code, rows, err = _run("count(), avg(total) by region", NDJSON)
    assert code is ExitCode.OK
    assert rows[0] == {"region": "EU", "count": 3, "avg_total": 75.0}
    assert rows[1] == {"region": "US", "count": 1, "avg_total": 10.0}
    assert "skipped 1 non-numeric value(s) of 'total'" in err


def test_no_by_is_one_row() -> None:
    _code, rows, _err = _run("count(), max(total)", NDJSON)
    assert rows == [{"count": 4, "max_total": 100.0}]


def test_plain_lines_count_fine() -> None:
    _code, rows, _err = _run("count()", "a\nb\n\nc\n")
    assert rows == [{"count": 3}]


def test_field_paths_end_to_end_with_the_lacking_census() -> None:
    ndjson = (
        '{"user": {"plan": "pro"}, "metrics": {"score": 10}}\n'
        '{"user": {"plan": "pro"}, "metrics": {"score": 30}}\n'
        '{"other": 1}\n'
    )
    code, rows, err = _run("count(), avg(metrics.score) by user.plan", ndjson)
    assert code is ExitCode.OK
    assert rows[0] == {"user.plan": "pro", "count": 2, "avg_metrics.score": 20.0}
    assert rows[1] == {"user.plan": None, "count": 1, "avg_metrics.score": None}
    assert "1 rows lacked 'user.plan' — grouped as null" in err


def test_rows_lacking_the_by_field_note_and_strict_errors(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import io as _io

    from smartpipe.verbs.summarize import SummarizeRequest, run_summarize

    stdin_text = '{"region": "eu", "total": 1}\n{"total": 2}\n'
    code = run_summarize(
        SummarizeRequest("count() by region"),
        stdin=_io.StringIO(stdin_text),
        stdout=_io.StringIO(),
    )
    assert code is ExitCode.OK
    assert "summarize: 1 rows lacked 'region' — grouped as null" in capsys.readouterr().err

    from smartpipe.core.errors import UsageFault

    with pytest.raises(UsageFault, match="lacked 'region'"):
        run_summarize(
            SummarizeRequest("count() by region", strict_rows=True),
            stdin=_io.StringIO(stdin_text),
            stdout=_io.StringIO(),
        )


def test_time_bin_strict_rows_checks_the_source_field_not_the_output_alias() -> None:
    stdout = io.StringIO()
    code = run_summarize(
        SummarizeRequest("count() by bin(ts, 1h)", strict_rows=True),
        stdin=io.StringIO('{"ts": "2026-07-01T15:01:00Z"}\n{"ts": "2026-07-01T15:59:00Z"}\n'),
        stdout=stdout,
    )
    assert code is ExitCode.OK
    assert [json.loads(line) for line in stdout.getvalue().splitlines()] == [
        {"ts_bin": "15:00", "count": 2}
    ]
