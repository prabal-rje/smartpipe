from __future__ import annotations

from pathlib import Path

import pytest

from sempipe import __version__
from tests.conftest import RunCli

GOLDEN = Path(__file__).parent / "golden"


def test_bare_invocation_prints_welcome_and_exits_zero(run_cli: RunCli) -> None:
    code, out, _err = run_cli([])
    assert code == 0
    assert out == (GOLDEN / "welcome.txt").read_text(encoding="utf-8")


def test_version(run_cli: RunCli) -> None:
    code, out, _err = run_cli(["--version"])
    assert code == 0
    assert out.strip() == f"sempipe {__version__}"


def test_help_exits_zero(run_cli: RunCli) -> None:
    code, out, _err = run_cli(["--help"])
    assert code == 0
    assert "Usage: sempipe" in out


def test_unknown_command_exits_64(run_cli: RunCli) -> None:
    code, _out, err = run_cli(["frobnicate"])
    assert code == 64
    assert err.startswith("error:")
    assert "frobnicate" in err


def test_unexpected_exception_is_the_bug_screen_exit_70(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _boom(*_args: object, **_kwargs: object) -> None:
        raise ValueError("wires crossed")

    monkeypatch.setattr("sempipe.cli.root.cli.main", _boom)
    code, _out, err = run_cli(["--version"])
    assert code == 70
    assert "internal error — this is a bug in sempipe" in err
    assert "ValueError: wires crossed" in err
    assert "Traceback" not in err  # hidden without --debug


def test_keyboard_interrupt_exits_130(run_cli: RunCli, monkeypatch: pytest.MonkeyPatch) -> None:
    def _interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("sempipe.cli.root.cli.main", _interrupt)
    code, _out, _err = run_cli(["--version"])
    assert code == 130
