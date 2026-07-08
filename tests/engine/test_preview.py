"""Media-preview arithmetic (pure): render gate, sampling, sizing, envelopes,
the play-link string. The io shell is tested in tests/io/test_preview.py."""

from __future__ import annotations

import pytest

from smartpipe.engine.preview import (
    human_duration,
    human_size,
    join_columns,
    peak_envelope,
    play_line,
    should_preview,
    strip_seconds,
    thumbnail_cells,
)

# --- the render-or-not decision ---------------------------------------------------


@pytest.mark.parametrize(
    ("enabled", "color", "expected"),
    [
        (True, True, True),
        (True, False, False),  # NO_COLOR / TERM=dumb / piped: byte-identical output
        (False, True, False),  # config media-previews off: today's behavior
        (False, False, False),
    ],
)
def test_should_preview_needs_the_switch_and_color(
    enabled: bool, color: bool, expected: bool
) -> None:
    assert should_preview(enabled=enabled, color=color) is expected


# --- video strip sampling ----------------------------------------------------------


def test_strip_samples_at_10_50_90_percent() -> None:
    assert strip_seconds(100.0) == (10.0, 50.0, 90.0)


def test_strip_never_samples_the_zero_frame() -> None:
    first, middle, last = strip_seconds(0.5)  # intros are black/logo frames
    assert 0.0 < first < middle < last < 0.5


# --- thumbnail sizing (terminal cells are ~twice as tall as wide) ------------------


@pytest.mark.parametrize(
    ("width", "height", "expected"),
    [
        (1920, 1080, (40, 11)),  # landscape pins to the column cap
        (1080, 1920, (14, 12)),  # portrait pins to the row cap
        (100, 100, (24, 12)),  # square: rows bound it, cols follow the aspect
        (4, 4, (24, 12)),  # tiny images scale up to a visible size
    ],
)
def test_thumbnail_cells_fit_the_cap_and_keep_aspect(
    width: int, height: int, expected: tuple[int, int]
) -> None:
    assert thumbnail_cells(width, height) == expected


def test_thumbnail_cells_honor_custom_caps() -> None:
    assert thumbnail_cells(1920, 1080, max_cols=12, max_rows=6) == (12, 3)


def test_thumbnail_cells_never_collapse_to_zero() -> None:
    assert thumbnail_cells(4000, 1, max_cols=40, max_rows=12) == (40, 1)
    assert thumbnail_cells(0, 100) == (1, 1)  # a lying header can't crash the preview


# --- waveform envelope --------------------------------------------------------------


def test_peak_envelope_of_nothing_is_empty() -> None:
    assert peak_envelope((), 40) == ()


def test_peak_envelope_normalizes_to_the_clips_own_peak() -> None:
    assert peak_envelope((1, -4, 2, 8), 2) == (0.5, 1.0)


def test_peak_envelope_of_silence_is_flat_zero() -> None:
    assert peak_envelope((0, 0, 0, 0), 2) == (0.0, 0.0)


def test_peak_envelope_never_makes_more_buckets_than_samples() -> None:
    assert peak_envelope((5, -10), 40) == (0.5, 1.0)


def test_peak_envelope_spreads_uneven_buckets() -> None:
    peaks = peak_envelope((1, 2, 3, 4, 5), 3)
    assert len(peaks) == 3
    assert peaks[-1] == 1.0


# --- human formatting ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("seconds", "expected"),
    [(0.0, "0:00"), (42.0, "0:42"), (59.9, "0:59"), (723.0, "12:03"), (3723.0, "1:02:03")],
)
def test_human_duration_is_a_clock(seconds: float, expected: str) -> None:
    assert human_duration(seconds) == expected


@pytest.mark.parametrize(
    ("size", "expected"),
    [(5, "5 B"), (49_152, "48 KB"), (2_202_009, "2.1 MB")],
)
def test_human_size_matches_the_media_summary_units(size: int, expected: str) -> None:
    assert human_size(size) == expected


# --- the play "button" (OSC 8) ------------------------------------------------------


def test_play_line_wraps_an_osc8_hyperlink() -> None:
    line = play_line(
        url="file:///tmp/call.wav", path="/tmp/call.wav", seconds=42.0, size=2_202_009, color=True
    )
    assert line == "\x1b]8;;file:///tmp/call.wav\x1b\\▶ play (0:42, 2.1 MB)\x1b]8;;\x1b\\"


def test_play_line_without_color_prints_the_plain_path() -> None:
    line = play_line(
        url="file:///tmp/call.wav", path="/tmp/call.wav", seconds=42.0, size=2_202_009, color=False
    )
    assert line == "▶ play /tmp/call.wav (0:42, 2.1 MB)"
    assert "\x1b" not in line


def test_play_line_with_unknown_duration_shows_size_only() -> None:
    line = play_line(url="file:///a", path="/a", seconds=None, size=49_152, color=True)
    assert line is not None
    assert "▶ play (48 KB)" in line


def test_play_line_is_none_without_an_on_disk_source() -> None:
    assert play_line(url=None, path=None, seconds=1.0, size=1, color=True) is None


# --- side-by-side strip assembly ----------------------------------------------------


def test_join_columns_of_nothing_is_empty() -> None:
    assert join_columns(()) == ()


def test_join_columns_joins_rows_with_a_gap() -> None:
    left = (["aa", "bb"], 2)
    right = (["cc", "dd"], 2)
    assert join_columns((left, right)) == ("aa  cc", "bb  dd")


def test_join_columns_pads_short_columns_with_spaces() -> None:
    tall = (["aa", "bb"], 2)
    short = (["c"], 1)
    assert join_columns((tall, short)) == ("aa  c", "bb   ")
