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


# --- temporal columns (ledger item 56) --------------------------------------------


def test_mixed_date_and_datetime_columns_order_temporally() -> None:
    # a date reads as its midnight: it lands between the previous evening and
    # the first second of its own day
    out, _ = _run(
        "ts",
        '{"ts": "2026-01-15T00:00:01"}\n{"ts": "2026-01-15"}\n{"ts": "2026-01-14T23:00:00"}\n',
    )
    assert out.splitlines() == [
        '{"ts": "2026-01-14T23:00:00"}',
        '{"ts": "2026-01-15"}',
        '{"ts": "2026-01-15T00:00:01"}',
    ]


def test_offsets_order_by_instant_not_by_text() -> None:
    # 09:00+05:30 is 03:30Z — lexicographic text order would invert these
    out, _ = _run("ts", '{"ts": "2026-01-15T04:00:00Z"}\n{"ts": "2026-01-15T09:00:00+05:30"}\n')
    assert out.splitlines() == [
        '{"ts": "2026-01-15T09:00:00+05:30"}',
        '{"ts": "2026-01-15T04:00:00Z"}',
    ]


def test_temporal_descending_flips_and_missing_stays_last() -> None:
    out, err = _run(
        "due",
        '{"due": "2026-01-01"}\n{"x": 1}\n{"due": "2026-03-01"}\n',
        descending=True,
    )
    assert out.splitlines() == ['{"due": "2026-03-01"}', '{"due": "2026-01-01"}', '{"x": 1}']
    assert "1 rows missing 'due' placed last" in err


def test_temporal_ties_stay_stable() -> None:
    out, _ = _run("due", '{"due": "2026-01-15", "id": 1}\n{"due": "2026-01-15T00:00", "id": 2}\n')
    assert out.splitlines() == [
        '{"due": "2026-01-15", "id": 1}',
        '{"due": "2026-01-15T00:00", "id": 2}',
    ]


def test_mixed_temporal_and_plain_columns_keep_the_existing_bands() -> None:
    # one non-ISO value → the whole column falls back to number/string bands
    out, _ = _run("v", '{"v": "2026-01-15"}\n{"v": "soonish"}\n{"v": 7}\n')
    assert out.splitlines() == ['{"v": 7}', '{"v": "2026-01-15"}', '{"v": "soonish"}']


# --- field paths (ledger item 63) ---------------------------------------------------


def test_sort_by_a_nested_path() -> None:
    out, _ = _run(
        "user.score",
        '{"user": {"score": 10}}\n{"user": {"score": 2}}\n{"user": {"score": 33}}\n',
    )
    assert out.splitlines() == [
        '{"user": {"score": 2}}',
        '{"user": {"score": 10}}',
        '{"user": {"score": 33}}',
    ]


def test_sort_by_an_index_path_and_missing_lands_last() -> None:
    out, err = _run(
        "items[0].total",
        '{"items": [{"total": 9}]}\n{"items": []}\n{"items": [{"total": 1}]}\n',
    )
    assert out.splitlines() == [
        '{"items": [{"total": 1}]}',
        '{"items": [{"total": 9}]}',
        '{"items": []}',
    ]
    assert "1 rows missing 'items[0].total' placed last" in err


def test_sort_dotted_literal_column_wins_over_the_path() -> None:
    out, _ = _run(
        "user.score",
        '{"user.score": 1, "user": {"score": 99}}\n{"user.score": 0, "user": {"score": 5}}\n',
    )
    assert out.splitlines() == [
        '{"user.score": 0, "user": {"score": 5}}',
        '{"user.score": 1, "user": {"score": 99}}',
    ]
