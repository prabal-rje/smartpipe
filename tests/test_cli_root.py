from __future__ import annotations

from pathlib import Path

import click
import pytest

from smartpipe import __version__
from tests.conftest import RunCli

GOLDEN = Path(__file__).parent / "golden"


def test_bare_invocation_prints_welcome_and_exits_zero(run_cli: RunCli) -> None:
    code, out, _err = run_cli([])
    assert code == 0
    assert out == (GOLDEN / "welcome.txt").read_text(encoding="utf-8")


def test_version(run_cli: RunCli) -> None:
    code, out, _err = run_cli(["--version"])
    assert code == 0
    assert out.strip() == f"smartpipe {__version__}"


def test_help_exits_zero(run_cli: RunCli) -> None:
    code, out, _err = run_cli(["--help"])
    assert code == 0
    assert "Usage: smartpipe" in out


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

    monkeypatch.setattr("smartpipe.cli.root.cli.main", _boom)
    code, _out, err = run_cli(["--version"])
    assert code == 70
    assert "internal error — this is a bug in smartpipe" in err
    assert "ValueError: wires crossed" in err
    assert "Traceback" not in err  # hidden without --debug


def test_keyboard_interrupt_exits_130(run_cli: RunCli, monkeypatch: pytest.MonkeyPatch) -> None:
    def _interrupt(*_args: object, **_kwargs: object) -> None:
        raise KeyboardInterrupt

    monkeypatch.setattr("smartpipe.cli.root.cli.main", _interrupt)
    code, _out, _err = run_cli(["--version"])
    assert code == 130


def test_click_abort_exits_130_without_the_bug_screen(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    def _abort(*_args: object, **_kwargs: object) -> None:
        raise click.Abort

    monkeypatch.setattr("smartpipe.cli.root.cli.main", _abort)
    code, _out, err = run_cli(["--version"])
    assert code == 130
    assert "internal error" not in err


# --- the update-check hooks (notify-next-run; plan/ux.md "update notice") -------


@pytest.fixture
def update_hooks(monkeypatch: pytest.MonkeyPatch) -> dict[str, int]:
    calls = {"begin": 0, "notify": 0}
    from smartpipe.io import update_check

    def count(name: str) -> object:
        def bump(*args: object, **kwargs: object) -> None:
            del args, kwargs
            calls[name] += 1

        return bump

    monkeypatch.setattr(update_check, "begin_background_check", count("begin"))
    monkeypatch.setattr(update_check, "emit_update_notice", count("notify"))
    return calls


def test_a_run_begins_the_check_and_notifies_at_the_end(
    run_cli: RunCli, update_hooks: dict[str, int]
) -> None:
    code, _out, _err = run_cli(["--version"])
    assert code == 0
    assert update_hooks == {"begin": 1, "notify": 1}


def test_a_failing_run_never_notifies(run_cli: RunCli, update_hooks: dict[str, int]) -> None:
    code, _out, _err = run_cli(["frobnicate"])
    assert code == 64
    assert update_hooks["notify"] == 0


def test_the_update_command_itself_never_notifies(
    run_cli: RunCli, update_hooks: dict[str, int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "smartpipe.cli.update_cmd.install_paths",
        lambda: ("/somewhere/python", "/somewhere/app"),  # unknown channel: no prompt
    )
    code, _out, _err = run_cli(["update"])
    assert code == 0
    assert update_hooks["notify"] == 0
