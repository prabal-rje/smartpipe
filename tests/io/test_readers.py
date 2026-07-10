from __future__ import annotations

import io

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.io.items import Item
from smartpipe.io.readers import ensure_not_a_tty, stdin_items


async def _collect(stdin: io.StringIO) -> list[Item]:
    return [item async for item in stdin_items(stdin)]


async def test_yields_items_in_order_with_contiguous_indexes() -> None:
    items = await _collect(io.StringIO("a\nb\nc\n"))
    assert [item.raw for item in items] == ["a", "b", "c"]
    assert [item.source.index for item in items] == [0, 1, 2]


async def test_final_line_without_newline_is_an_item() -> None:
    items = await _collect(io.StringIO("a\nb"))
    assert [item.raw for item in items] == ["a", "b"]


async def test_empty_stdin_yields_nothing() -> None:
    assert await _collect(io.StringIO("")) == []


async def test_crlf_input() -> None:
    items = await _collect(io.StringIO("a\r\nb\r\n"))
    assert [item.raw for item in items] == ["a", "b"]


async def test_empty_lines_are_items() -> None:
    items = await _collect(io.StringIO("a\n\nb\n"))
    assert [item.raw for item in items] == ["a", "", "b"]


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_tty_stdin_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault) as excinfo:
        ensure_not_a_tty(_FakeTty())
    assert "pipe some input" in str(excinfo.value)


def test_piped_stdin_passes_the_guard() -> None:
    ensure_not_a_tty(io.StringIO("data\n"))  # must not raise


# --- scan routing disclosure (D39/03) ----------------------------------------------


def test_thin_text_with_figures_reads_as_a_scan() -> None:
    from smartpipe.io.readers import figure_note

    note = figure_note("contract.pdf", 11, 8, 22)
    assert "thin text layer (11 chars)" in note
    assert "scanned?" in note
    assert "split --by pages --media" in note  # the actionable past-the-cap hint


def test_real_text_keeps_the_plainfigure_note() -> None:
    from smartpipe.io.readers import figure_note

    note = figure_note("report.pdf", 5_000, 3, 0)
    assert note == "report.pdf: 3 figures attached"
    assert "scanned" not in note


# --- the kind census (wave 2, item 20) ---------------------------------------------


async def test_mixed_stream_notes_the_census_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import io as _io

    from smartpipe.io.readers import stdin_items

    stream = _io.StringIO('{"a": 1}\nplain\n{"b": 2}\n')
    _ = [item async for item in stdin_items(stream)]
    err = capsys.readouterr().err
    assert "input: 2 records · 1 plain lines" in err


async def test_pure_streams_stay_silent(capsys: pytest.CaptureFixture[str]) -> None:
    import io as _io

    from smartpipe.io.readers import stdin_items

    stream = _io.StringIO('{"a": 1}\n{"b": 2}\n')
    _ = [item async for item in stdin_items(stream)]
    assert "input:" not in capsys.readouterr().err


async def test_strict_rows_raises_early_naming_the_mixed_row() -> None:
    import io as _io

    import pytest as _pytest

    from smartpipe.core.errors import UsageFault
    from smartpipe.io.readers import stdin_items

    stream = _io.StringIO('{"a": 1}\nplain\n{"b": 2}\n')
    collected: list[Item] = []
    with _pytest.raises(UsageFault) as excinfo:
        async for item in stdin_items(stream, strict_rows=True):
            collected.append(item)
    message = str(excinfo.value)
    assert "line 2 is a plain text line in a record stream" in message
    assert "--strict-rows demands one kind" in message
    # early: the offending row never reaches the verb, nor does anything after it
    assert [item.raw for item in collected] == ['{"a": 1}']


async def test_strict_rows_names_a_record_in_a_plain_stream() -> None:
    import io as _io

    import pytest as _pytest

    from smartpipe.core.errors import UsageFault
    from smartpipe.io.readers import stdin_items

    stream = _io.StringIO('plain\n{"a": 1}\n')
    with _pytest.raises(UsageFault, match="line 2 is a record in a plain-text stream"):
        _ = [item async for item in stdin_items(stream, strict_rows=True)]


async def test_strict_rows_env_var_matches_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io as _io

    import pytest as _pytest

    from smartpipe.core.errors import UsageFault
    from smartpipe.io.readers import stdin_items

    monkeypatch.setenv("SMARTPIPE_STRICT_ROWS", "1")
    stream = _io.StringIO('{"a": 1}\nplain\n')
    with _pytest.raises(UsageFault, match="line 2 is a plain text line"):
        _ = [item async for item in stdin_items(stream)]
