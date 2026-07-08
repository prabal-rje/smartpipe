"""The hand-rolled menu: key decoding, cursor math, capability, numbered fallback."""

from __future__ import annotations

import pytest

from smartpipe.io.arrow_menu import (
    MenuKey,
    decode_key,
    menu_capable,
    numbered_choose,
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
    assert step(0, MenuKey.UP, 3) == 2
    assert step(2, MenuKey.DOWN, 3) == 0
    assert step(1, MenuKey.DOWN, 3) == 2
    assert step(1, MenuKey.OTHER, 3) == 1
    assert step(1, MenuKey.ENTER, 3) == 1


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
    assert said[1] == "  1. ollama  4 local models"
    assert said[2] == "  2. openai  API key"


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
