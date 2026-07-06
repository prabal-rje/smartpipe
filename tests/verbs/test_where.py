"""The where verb: free, streaming, passthrough, honest about silence."""

from __future__ import annotations

import io

import pytest

from sempipe.core.errors import ExitCode, UsageFault
from sempipe.verbs.where import WhereRequest, run_where


def _run(predicate: str, stdin: str) -> tuple[ExitCode, str, str]:
    out = io.StringIO()
    import contextlib

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_where(WhereRequest(predicate), stdin=io.StringIO(stdin), stdout=out)
    return code, out.getvalue(), err.getvalue()


def test_passthrough_is_byte_faithful_and_ordered() -> None:
    lines = '{"n": 2,   "x":"keep"}\n{"n": 1}\n{"n": 3,"x":"keep"}\n'
    code, out, _err = _run("n >= 2", lines)
    assert code is ExitCode.OK
    assert out == '{"n": 2,   "x":"keep"}\n{"n": 3,"x":"keep"}\n'  # spacing survives


def test_zero_matches_is_exit_zero_and_empty() -> None:
    code, out, err = _run('text has "absent"', "a\nb\n")
    assert code is ExitCode.OK
    assert out == ""
    assert "0 of 2 matched" in err


def test_count_line_and_missing_rollup() -> None:
    _code, _out, err = _run('level == "error"', '{"level": "error"}\n{"other": 1}\nplain\n')
    assert "1 of 3 matched" in err
    assert "field 'level' missing on 2 rows" in err


def test_plain_lines_match_on_text() -> None:
    _code, out, _err = _run('text contains "err"', "an ERRor\nfine\n")
    assert out == "an ERRor\n"


def test_bad_grammar_raises_before_reading_stdin() -> None:
    exploding = io.StringIO("should never be read")
    with pytest.raises(UsageFault, match="Operators"):
        run_where(WhereRequest("total >>> 5"), stdin=exploding, stdout=io.StringIO())
    assert exploding.tell() == 0  # stdin untouched — the fault fires at argv time


def test_blank_lines_are_ignored_not_counted() -> None:
    _code, _out, err = _run('text has "x"', "x\n\n\nx\n")
    assert "2 of 2 matched" in err
