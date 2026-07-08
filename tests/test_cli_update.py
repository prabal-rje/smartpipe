"""``smartpipe update`` — consented, honest, channel-aware.

The command must never run an upgrade tool without saying exactly what it
detected and what it will run; an unrecognized channel is guidance (exit 0),
a failed upgrade tool is a setup fault (exit 2), and a declined prompt
changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import pytest

from smartpipe.cli.update_cmd import run_update
from smartpipe.core.errors import SetupFault
from smartpipe.core.install_channel import Channel, detect_channel
from tests.conftest import RunCli

if TYPE_CHECKING:
    from collections.abc import Sequence


class _Recorder:
    def __init__(self, exit_code: int = 0) -> None:
        self.exit_code = exit_code
        self.said: list[str] = []
        self.executed: list[tuple[str, ...]] = []
        self.asked: list[str] = []

    def say(self, message: str) -> None:
        self.said.append(message)

    def execute(self, argv: Sequence[str]) -> int:
        self.executed.append(tuple(argv))
        return self.exit_code

    def confirm(self, question: str, *, answer: bool) -> bool:
        self.asked.append(question)
        return answer


# --- run_update (unit, injected I/O) --------------------------------------------


def test_consented_upgrade_runs_the_detected_command() -> None:
    rec = _Recorder()
    run_update(
        channel=Channel.UV_TOOL,
        version="1.4.0",
        assume_yes=False,
        confirm=lambda q: rec.confirm(q, answer=True),
        say=rec.say,
        execute=rec.execute,
    )
    assert rec.executed == [("uv", "tool", "upgrade", "smartpipe-cli")]
    assert rec.asked == ["Proceed?"]
    text = "\n".join(rec.said)
    assert "installed with uv tool" in text
    assert "will run: uv tool upgrade smartpipe-cli" in text
    assert "done — uv tool upgrade smartpipe-cli finished cleanly" in text


def test_declined_upgrade_changes_nothing() -> None:
    rec = _Recorder()
    run_update(
        channel=Channel.PIPX,
        version="1.4.0",
        assume_yes=False,
        confirm=lambda q: rec.confirm(q, answer=False),
        say=rec.say,
        execute=rec.execute,
    )
    assert rec.executed == []
    assert any("nothing changed" in line for line in rec.said)


def test_assume_yes_skips_the_prompt() -> None:
    rec = _Recorder()
    run_update(
        channel=Channel.HOMEBREW,
        version="1.4.0",
        assume_yes=True,
        confirm=lambda q: rec.confirm(q, answer=False),  # would decline if asked
        say=rec.say,
        execute=rec.execute,
    )
    assert rec.asked == []
    assert rec.executed == [("brew", "upgrade", "smartpipe")]


def test_failed_upgrade_is_a_setup_fault_naming_the_command() -> None:
    rec = _Recorder(exit_code=3)
    with pytest.raises(SetupFault) as caught:
        run_update(
            channel=Channel.PIP,
            version="1.4.0",
            assume_yes=True,
            confirm=lambda q: rec.confirm(q, answer=True),
            say=rec.say,
            execute=rec.execute,
        )
    message = str(caught.value)
    assert "exit 3" in message
    assert "pip install -U smartpipe-cli" in message


def test_unknown_channel_is_guidance_not_an_error() -> None:
    rec = _Recorder()
    run_update(
        channel=Channel.UNKNOWN,
        version="1.4.0",
        assume_yes=True,
        confirm=lambda q: rec.confirm(q, answer=True),
        say=rec.say,
        execute=rec.execute,
    )
    assert rec.executed == []
    text = "\n".join(rec.said)
    for line in (
        "brew upgrade smartpipe",
        "uv tool upgrade smartpipe-cli",
        "pipx upgrade smartpipe-cli",
        "pip install -U smartpipe-cli",
    ):
        assert line in text


# --- the CLI shell ---------------------------------------------------------------


def test_cli_unknown_channel_exits_zero(run_cli: RunCli, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "smartpipe.cli.update_cmd.install_paths",
        lambda: ("/dev/checkout/.venv/bin/python", "/dev/checkout/src/smartpipe"),
    )
    code, out, _err = run_cli(["update"])
    assert code == 0
    assert "install channel not recognized" in out
    assert "pipx upgrade smartpipe-cli" in out


def test_cli_decline_leaves_everything_alone(
    run_cli: RunCli, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(
        "smartpipe.cli.update_cmd.install_paths",
        lambda: ("/x/pipx/venvs/smartpipe-cli/bin/python", "/x/pipx/venvs/smartpipe-cli/sp"),
    )

    def never(_argv: Sequence[str]) -> int:
        raise AssertionError("a declined prompt must not execute anything")

    monkeypatch.setattr("smartpipe.cli.update_cmd.execute_upgrade", never)
    code, out, _err = run_cli(["update"], stdin="n\n")
    assert code == 0
    assert "will run: pipx upgrade smartpipe-cli" in out
    assert "nothing changed" in out


def test_cli_failed_tool_exits_two(run_cli: RunCli, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "smartpipe.cli.update_cmd.install_paths",
        lambda: ("/x/pipx/venvs/smartpipe-cli/bin/python", "/x/pipx/venvs/smartpipe-cli/sp"),
    )

    def fail(_argv: Sequence[str]) -> int:
        return 1

    monkeypatch.setattr("smartpipe.cli.update_cmd.execute_upgrade", fail)
    code, _out, err = run_cli(["update", "--yes"])
    assert code == 2
    assert "error: the upgrade command failed (exit 1)" in err
    assert "pipx upgrade smartpipe-cli" in err


def test_cli_help_shows_examples(run_cli: RunCli) -> None:
    code, out, _err = run_cli(["update", "--help"])
    assert code == 0
    assert "Examples:" in out
    assert "--yes" in out


def test_cli_eof_at_the_prompt_declines(run_cli: RunCli, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "smartpipe.cli.update_cmd.install_paths",
        lambda: ("/x/pipx/venvs/smartpipe-cli/bin/python", "/x/pipx/venvs/smartpipe-cli/sp"),
    )
    code, out, _err = run_cli(["update"], stdin="")
    assert code == 0
    assert "nothing changed" in out


# --- the real process boundaries (no upgrade tool ever runs) ---------------------


def test_install_paths_reads_this_process() -> None:
    from smartpipe.cli.update_cmd import install_paths

    executable, module_path = install_paths()
    assert executable and module_path
    assert "smartpipe" in module_path
    assert isinstance(Channel(detect_channel(executable, module_path)), Channel)


def test_execute_upgrade_reports_the_child_exit_code() -> None:
    import sys

    from smartpipe.cli.update_cmd import execute_upgrade

    assert execute_upgrade([sys.executable, "-c", "raise SystemExit(0)"]) == 0
    assert execute_upgrade([sys.executable, "-c", "raise SystemExit(5)"]) == 5


def test_execute_upgrade_missing_tool_is_a_setup_fault() -> None:
    from smartpipe.cli.update_cmd import execute_upgrade

    with pytest.raises(SetupFault, match="isn't on PATH"):
        execute_upgrade(["smartpipe-no-such-upgrade-tool-xyz"])
