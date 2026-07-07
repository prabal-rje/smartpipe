"""The sort verb: typed bands, stable ties, missing-last honesty."""

from __future__ import annotations

import io

from smartpipe.core.errors import ExitCode
from smartpipe.verbs.sortverb import SortRequest, run_sort


def _run(by: str, stdin_text: str, *, descending: bool = False) -> tuple[str, str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_sort(
            SortRequest(by=by, descending=descending), stdin=io.StringIO(stdin_text), stdout=out
        )
    assert code is ExitCode.OK
    return out.getvalue(), err.getvalue()


def test_numbers_sort_numerically() -> None:
    out, _ = _run("score", '{"score": 10}\n{"score": 2}\n{"score": 33}\n')
    assert out.splitlines() == ['{"score": 2}', '{"score": 10}', '{"score": 33}']


def test_desc_flips_and_missing_still_lands_last() -> None:
    out, err = _run("score", '{"score": 1}\n{"other": 9}\n{"score": 5}\n', descending=True)
    assert out.splitlines() == ['{"score": 5}', '{"score": 1}', '{"other": 9}']
    assert "1 rows missing 'score' placed last" in err


def test_strings_sort_lexically_after_numbers() -> None:
    out, _ = _run("v", '{"v": "b"}\n{"v": 7}\n{"v": "a"}\n')
    assert out.splitlines() == ['{"v": 7}', '{"v": "a"}', '{"v": "b"}']


def test_desc_strings_reverse() -> None:
    out, _ = _run("v", '{"v": "b"}\n{"v": "a"}\n', descending=True)
    assert out.splitlines() == ['{"v": "b"}', '{"v": "a"}']


def test_stable_on_ties_and_byte_faithful() -> None:
    out, _ = _run("s", '{"s": 1,   "id": "first"}\n{"s": 1, "id": "second"}\n')
    assert out.splitlines()[0] == '{"s": 1,   "id": "first"}'  # tie keeps order, bytes kept
