"""``display_width``/``clip_to_width`` — terminal cells, not code points (DEFER-2).

Stdlib-only by design (no ``wcwidth`` dep): combining marks are 0, East-Asian
Wide/Fullwidth are 2, zero-width format characters are 0, everything else is 1.
Emoji-ZWJ sequences are pinned as the *sum of parts* — approximate on purpose.
"""

from __future__ import annotations

from sempipe.io.text import clip_to_width, display_width


def test_ascii_is_one_cell_per_char() -> None:
    assert display_width("abc") == 3


def test_east_asian_wide_is_two_cells() -> None:
    assert display_width("名前") == 4


def test_halfwidth_katakana_is_one_cell() -> None:
    assert display_width("ｱ") == 1


def test_combining_mark_adds_nothing() -> None:
    assert display_width("é") == 1  # é as base + combining acute


def test_precomposed_accent_is_one_cell() -> None:
    assert display_width("é") == 1


def test_zwj_sequence_is_the_sum_of_parts() -> None:
    # pinned approximation: no grapheme clustering; the joiner itself is zero
    assert display_width("👩‍👩") == 4


def test_empty_is_zero() -> None:
    assert display_width("") == 0


# --- clipping ---------------------------------------------------------------------


def test_clip_keeps_text_that_fits() -> None:
    assert clip_to_width("abc", 5) == "abc"


def test_clip_cuts_by_cells_not_chars() -> None:
    assert clip_to_width("名前名前", 5) == "名前"  # 3rd wide char would make 6 cells


def test_clip_never_splits_a_wide_char() -> None:
    assert display_width(clip_to_width("a名名", 4)) <= 4
    assert clip_to_width("a名名", 4) == "a名"


def test_clip_to_zero_is_empty() -> None:
    assert clip_to_width("abc", 0) == ""
