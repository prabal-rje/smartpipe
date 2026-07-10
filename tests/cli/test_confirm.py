"""confirm_on_stderr: the stdout-silent prompt (matrix-caught Windows leak)."""

from __future__ import annotations

import io

import pytest

from smartpipe.cli.confirm import confirm_on_stderr

__all__ = []


@pytest.mark.parametrize(
    ("typed", "default", "expected"),
    [
        ("", True, True),  # Enter takes the default
        ("", False, False),
        ("y", False, True),
        ("yes", False, True),
        ("n", True, False),
        ("no", True, False),
        ("  Y  ", False, True),  # whitespace + case folded
        ("whatever", True, False),  # non-yes text is a decline
    ],
)
def test_answers(
    monkeypatch: pytest.MonkeyPatch,
    capsys: pytest.CaptureFixture[str],
    typed: str,
    default: bool,
    expected: bool,
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(typed + "\n"))
    assert confirm_on_stderr("proceed?", default=default) is expected
    captured = capsys.readouterr()
    assert captured.out == ""  # stdout is sacred - not one byte, on any platform
    suffix = "[Y/n]" if default else "[y/N]"
    assert captured.err == f"proceed? {suffix}: "


def test_eof_declines_and_lands_the_cursor(
    monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    monkeypatch.setattr("sys.stdin", io.StringIO(""))  # EOF at the prompt
    assert confirm_on_stderr("proceed?", default=True) is False
    captured = capsys.readouterr()
    assert captured.out == ""
    assert captured.err == "proceed? [Y/n]: \n"
