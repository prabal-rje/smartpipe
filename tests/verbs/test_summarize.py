"""The summarize verb: one pass, ranked groups, disclosed skips."""

from __future__ import annotations

import io
import json

from sempipe.core.errors import ExitCode
from sempipe.verbs.summarize import SummarizeRequest, run_summarize


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
