from __future__ import annotations

from pathlib import Path

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
