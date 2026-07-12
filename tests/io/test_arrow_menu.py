"""The hand-rolled menu: key decoding, cursor math, capability, numbered fallback."""

from __future__ import annotations

from collections.abc import Callable

import pytest

from smartpipe.io.arrow_menu import (
    SEPARATOR,
    MenuKey,
    decode_key,
    first_selectable,
    index_of_ordinal,
    menu_capable,
    numbered_choose,
    ordinal_of,
    read_sequence,
    render_menu,
    step,
)

# --- key decoding ------------------------------------------------------------------


@pytest.mark.parametrize(
    ("sequence", "expected"),
    [
        ("\x1b[A", MenuKey.UP),
        ("\x1bOA", MenuKey.UP),
        ("k", MenuKey.UP),
        ("\x1b[B", MenuKey.DOWN),
        ("\x1bOB", MenuKey.DOWN),
        ("j", MenuKey.DOWN),
        ("\r", MenuKey.ENTER),
        ("\n", MenuKey.ENTER),
        ("\x1b", MenuKey.CANCEL),
        ("q", MenuKey.CANCEL),
        ("x", MenuKey.OTHER),
        ("", MenuKey.OTHER),
        ("\x1b[C", MenuKey.OTHER),  # right arrow does nothing
    ],
)
def test_decode_key(sequence: str, expected: MenuKey) -> None:
    assert decode_key(sequence) is expected


def test_step_wraps_both_ways() -> None:
    labels = ("a", "b", "c")
    assert step(0, MenuKey.UP, labels) == 2
    assert step(2, MenuKey.DOWN, labels) == 0
    assert step(1, MenuKey.DOWN, labels) == 2
    assert step(1, MenuKey.OTHER, labels) == 1
    assert step(1, MenuKey.ENTER, labels) == 1


def test_step_slides_past_separators_both_ways() -> None:
    labels = ("a", SEPARATOR, "b", SEPARATOR, "c")
    assert step(0, MenuKey.DOWN, labels) == 2
    assert step(2, MenuKey.DOWN, labels) == 4
    assert step(4, MenuKey.UP, labels) == 2
    assert step(2, MenuKey.UP, labels) == 0
    assert step(0, MenuKey.UP, labels) == 4  # wraps over the tail separator
    assert step(4, MenuKey.DOWN, labels) == 0  # and back around


def test_step_all_separators_stays_put() -> None:
    labels = (SEPARATOR, SEPARATOR)
    assert step(0, MenuKey.DOWN, labels) == 0
    assert step(1, MenuKey.UP, labels) == 1


def test_first_selectable_skips_and_wraps() -> None:
    labels = (SEPARATOR, "a", SEPARATOR, "b")
    assert first_selectable(labels, 0) == 1
    assert first_selectable(labels, 1) == 1
    assert first_selectable(labels, 2) == 3
    assert first_selectable(("a", SEPARATOR), 1) == 0  # wraps to the front
    assert first_selectable((SEPARATOR,), 0) == 0  # degenerate: nothing selectable


def test_ordinal_mapping_round_trips_over_separators() -> None:
    labels = ("a", SEPARATOR, "b", SEPARATOR, "c")
    assert [ordinal_of(labels, i) for i in (0, 2, 4)] == [1, 2, 3]
    assert [index_of_ordinal(labels, n) for n in (1, 2, 3)] == [0, 2, 4]
    assert index_of_ordinal(labels, 0) is None
    assert index_of_ordinal(labels, 4) is None  # a digit can never reach a separator


# --- capability ---------------------------------------------------------------------


def test_menu_capable_needs_two_ttys_and_a_real_term() -> None:
    assert menu_capable(stdin_tty=True, stdout_tty=True, term="xterm-256color")
    assert not menu_capable(stdin_tty=False, stdout_tty=True, term="xterm")
    assert not menu_capable(stdin_tty=True, stdout_tty=False, term="xterm")
    assert not menu_capable(stdin_tty=True, stdout_tty=True, term="dumb")
    assert menu_capable(stdin_tty=True, stdout_tty=True, term=None)  # windows consoles


# --- rendering (pure frame) -----------------------------------------------------------


def test_render_menu_marks_the_selected_row() -> None:
    marker = "\u276f"  # the cursor mark, spelled as an escape so RUF001 stays quiet
    frame = render_menu(("alpha", "beta"), 1)
    lines = frame.splitlines()
    assert len(lines) == 2
    assert "alpha" in lines[0] and marker not in lines[0]
    assert "beta" in lines[1] and marker in lines[1]
    assert frame.endswith("\n")


def test_render_menu_separator_is_a_cleared_blank_row() -> None:
    marker = "\u276f"
    frame = render_menu(("alpha", SEPARATOR, "beta"), 0)
    lines = frame.splitlines()
    assert len(lines) == 3  # a separator still occupies a frame row
    assert lines[1] == "\x1b[2K"  # cleared, no indent, no marker
    assert marker not in lines[1]


# --- numbered fallback -----------------------------------------------------------------


def test_numbered_choose_enter_takes_the_default() -> None:
    said: list[str] = []
    picked = numbered_choose(
        "Pick a provider:",
        ("ollama  4 local models", "openai  API key"),
        1,
        ask=lambda _q, default: default,
        say=said.append,
    )
    assert picked == 1  # the start index becomes the default answer
    assert said[0] == "Pick a provider:"
    assert said[1] == ""  # breathing room between the title and the rows
    assert said[2] == "  1. ollama  4 local models"
    assert said[3] == "  2. openai  API key"


def test_numbered_choose_separators_print_blank_and_take_no_number() -> None:
    said: list[str] = []
    asked: list[tuple[str, str]] = []

    def ask(question: str, default: str) -> str:
        asked.append((question, default))
        return default

    labels = ("ollama", SEPARATOR, "keep current", SEPARATOR, "skip", "cancel")
    picked = numbered_choose("Pick:", labels, 2, ask=ask, say=said.append)
    assert said == [
        "Pick:",
        "",
        "  1. ollama",
        "",
        "  2. keep current",
        "",
        "  3. skip",
        "  4. cancel",
    ]
    assert asked[0] == ("Pick [1-4]", "2")  # 4 selectable rows; default = keep's ordinal
    assert picked == 2  # …and the answer maps back to keep's RAW index


def test_numbered_choose_start_on_a_separator_normalizes_forward() -> None:
    labels = ("a", SEPARATOR, "b")
    picked = numbered_choose("Pick:", labels, 1, ask=lambda _q, d: d, say=lambda _l: None)
    assert picked == 2  # default slid off the blank row to the next selectable


def test_numbered_choose_typed_digit_never_lands_on_a_separator() -> None:
    labels = ("a", SEPARATOR, "b")
    picked = numbered_choose("Pick:", labels, 0, ask=lambda _q, _d: "2", say=lambda _l: None)
    assert picked == 2  # ordinal 2 = "b" at raw index 2, not the blank row at 1


def test_numbered_choose_blank_answer_never_matches_a_separator() -> None:
    strikes: list[str] = []

    def ask(question: str, _default: str) -> str:
        strikes.append(question)
        return "   "  # whitespace — must not label-match the "" separator row

    labels = ("a", SEPARATOR, "b")
    assert numbered_choose("Pick:", labels, 0, ask=ask, say=lambda _l: None) is None
    assert len(strikes) == 2  # two strikes, out — never a silent separator pick


def test_numbered_choose_by_number() -> None:
    picked = numbered_choose(
        "Pick:", ("a", "b", "c"), 0, ask=lambda _q, _d: "3", say=lambda _line: None
    )
    assert picked == 2


def test_numbered_choose_by_exact_label_or_first_word() -> None:
    labels = ("openai      API key", "gemini      GEMINI_API_KEY")
    assert (
        numbered_choose("Pick:", labels, 0, ask=lambda _q, _d: "gemini", say=lambda _l: None) == 1
    )
    assert (
        numbered_choose("Pick:", labels, 0, ask=lambda _q, _d: labels[0], say=lambda _l: None) == 0
    )


def test_numbered_choose_two_strikes_then_none() -> None:
    asked: list[str] = []

    def ask(question: str, default: str) -> str:
        asked.append(question)
        return "42"  # out of range, twice

    assert numbered_choose("Pick:", ("a", "b"), 0, ask=ask, say=lambda _l: None) is None
    assert len(asked) == 2
    assert "number" in asked[1]  # the reprompt names what it wants


def _feeder(
    *chars: str,
) -> tuple[list[str], tuple[Callable[[], str], Callable[[], bool]]]:
    queue = list(chars)

    def read1() -> str:
        return queue.pop(0)

    def pending() -> bool:
        return bool(queue)

    return queue, (read1, pending)


def test_read_sequence_assembles_a_csi_arrow() -> None:
    """The owner-hit bug: all three arrow bytes arrive together; a buffered
    reader swallowed the tail and a bare ESC cancelled the picker."""
    _, (read1, pending) = _feeder("\x1b", "[", "A")
    assert read_sequence(read1, pending) == "\x1b[A"


def test_read_sequence_assembles_an_ss3_arrow() -> None:
    _, (read1, pending) = _feeder("\x1b", "O", "B")
    assert read_sequence(read1, pending) == "\x1bOB"


def test_read_sequence_bare_escape_stays_cancel() -> None:
    _, (read1, pending) = _feeder("\x1b")
    assert read_sequence(read1, pending) == "\x1b"


def test_read_sequence_plain_key_returns_immediately() -> None:
    queue, (read1, pending) = _feeder("j", "x")
    assert read_sequence(read1, pending) == "j"
    assert queue == ["x"]  # never over-reads


def test_read_sequence_ctrl_c_raises() -> None:
    import pytest

    _, (read1, pending) = _feeder("\x03")
    with pytest.raises(KeyboardInterrupt):
        read_sequence(read1, pending)
