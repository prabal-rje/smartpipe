"""The determinate progress bar (ledger item 67): every state is a table test,
and the flagship strings are pinned byte-for-byte — the bar is a UX contract."""

from __future__ import annotations

import math

import pytest

from smartpipe.engine.progressbar import format_eta, render_bar

# --- the pinned flagship shapes ---------------------------------------------------


def test_the_pinned_unicode_bar_line() -> None:
    assert render_bar(205, 500, rate=12.0) == "[██████░░░░░░░░░] 41% · 205/500 · 12/s · ~25s left"


def test_the_pinned_ascii_bar_line() -> None:
    assert (
        render_bar(6, 10, rate=3.0, width=10, ascii_only=True)
        == "[=====>....] 60% · 6/10 · 3.0/s · ~1s left"
    )


def test_the_pinned_stage_labelled_bar_line() -> None:
    assert (
        render_bar(205, 500, rate=12.0, label="extract")
        == "[extract] [██████░░░░░░░░░] 41% · 205/500 · 12/s · ~25s left"
    )


# --- start and finish states ------------------------------------------------------


def test_done_zero_has_no_eta_and_an_empty_bar() -> None:
    assert render_bar(0, 500, rate=0.0) == "[░░░░░░░░░░░░░░░] 0% · 0/500"


def test_done_zero_with_a_rate_still_hides_the_eta() -> None:
    assert render_bar(0, 500, rate=12.0) == "[░░░░░░░░░░░░░░░] 0% · 0/500 · 12/s"


def test_done_equals_total_is_full_with_no_left_segment() -> None:
    assert render_bar(500, 500, rate=12.0) == "[███████████████] 100% · 500/500 · 12/s"


def test_ascii_empty_and_full_bars_have_no_arrow_head() -> None:
    assert render_bar(0, 10, rate=0.0, width=10, ascii_only=True) == "[..........] 0% · 0/10"
    assert (
        render_bar(10, 10, rate=5.0, width=10, ascii_only=True)
        == "[==========] 100% · 10/10 · 5.0/s"
    )


def test_the_bar_never_fills_before_the_last_item() -> None:
    # 499/500 truncates to 99% and 14 of 15 cells — full is earned, not rounded
    assert render_bar(499, 500, rate=12.0).startswith("[██████████████░] 99% ")


# --- absurd inputs stay calm --------------------------------------------------------


@pytest.mark.parametrize("rate", [0.0, -3.0, math.inf, math.nan])
def test_unusable_rates_drop_the_rate_and_eta_segments(rate: float) -> None:
    assert render_bar(205, 500, rate=rate) == "[██████░░░░░░░░░] 41% · 205/500"


def test_a_huge_rate_reads_zero_seconds_left() -> None:
    assert render_bar(205, 500, rate=1e9).endswith(" · ~0s left")


def test_done_beyond_total_clamps_the_bar_and_percent() -> None:
    # a defensive clamp: counts stay honest, the bar and percent cap at 100
    assert render_bar(7, 5, rate=1.0) == "[███████████████] 100% · 7/5 · 1.0/s"


def test_zero_total_renders_complete() -> None:
    assert render_bar(0, 0, rate=0.0) == "[███████████████] 100% · 0/0"


# --- width ---------------------------------------------------------------------------


def test_width_floors_at_five_cells() -> None:
    assert render_bar(1, 2, rate=1.0, width=0) == "[██░░░] 50% · 1/2 · 1.0/s · ~1s left"
    assert render_bar(1, 2, rate=1.0, width=3) == "[██░░░] 50% · 1/2 · 1.0/s · ~1s left"


def test_wider_bars_scale_the_fill() -> None:
    assert render_bar(5, 10, rate=2.0, width=20).startswith("[██████████░░░░░░░░░░] 50% ")


# --- rate and eta formatting ----------------------------------------------------------


def test_slow_rates_keep_one_decimal_fast_rates_go_integer() -> None:
    assert " · 0.5/s · " in render_bar(50, 100, rate=0.5)
    assert " · 9.9/s · " in render_bar(50, 100, rate=9.9)
    assert " · 10/s · " in render_bar(50, 100, rate=10.0)
    assert " · 120/s · " in render_bar(50, 100, rate=120.4)


def test_eta_rounds_to_the_nearest_second() -> None:
    # (500 - 205) / 12 = 24.58… → 25, matching the pinned line
    assert render_bar(205, 500, rate=12.0).endswith("~25s left")
    # (100 - 50) / 0.4 = 125 s → minutes+seconds
    assert render_bar(50, 100, rate=0.4).endswith("~2m5s left")


def test_long_etas_read_hours_and_minutes() -> None:
    assert render_bar(1, 100_000, rate=0.02).endswith("~1388h52m left")


def test_format_eta() -> None:
    assert format_eta(45) == "45s"
    assert format_eta(132) == "2m12s"
    assert format_eta(3723) == "1h2m"
    assert format_eta(0) == "0s"
