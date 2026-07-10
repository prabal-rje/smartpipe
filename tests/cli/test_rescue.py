"""The NO_MODEL rescue wizard (item 50): TTY-gated, decline-safe, always exit 2."""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from smartpipe.cli import screens
from smartpipe.cli.rescue import RESCUE_PROMPT, SAVED_RERUN, die_with_rescue, rescue_capable
from smartpipe.core.errors import SetupFault
from tests.conftest import RunCli

GOLDEN = Path(__file__).parents[1] / "golden" / "screens"

# --- the gate (pure) --------------------------------------------------------------


def test_gate_wants_a_real_terminal_on_both_ends() -> None:
    assert rescue_capable({"TERM": "xterm-256color"}, stdin_tty=True, stdout_tty=True)
    assert not rescue_capable({"TERM": "xterm-256color"}, stdin_tty=False, stdout_tty=True)
    assert not rescue_capable({"TERM": "xterm-256color"}, stdin_tty=True, stdout_tty=False)


def test_gate_refuses_dumb_terminals_and_ci() -> None:
    assert not rescue_capable({"TERM": "dumb"}, stdin_tty=True, stdout_tty=True)
    assert not rescue_capable({"CI": "1"}, stdin_tty=True, stdout_tty=True)
    assert not rescue_capable({"GITHUB_ACTIONS": "true"}, stdin_tty=True, stdout_tty=True)
    assert rescue_capable({"CI": ""}, stdin_tty=True, stdout_tty=True)  # empty = unset


# --- the wizard paths (injected wiring) --------------------------------------------


def _fault() -> SetupFault:
    return SetupFault(screens.NO_MODEL)


def test_non_tty_is_byte_identical_to_today(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as excinfo:
        die_with_rescue(_fault(), debug=False, capable=False)
    assert excinfo.value.code == 2
    err = capsys.readouterr().err
    assert err == screens.NO_MODEL + "\n"  # the plain screen — no question asked


def test_decline_keeps_todays_outcome(capsys: pytest.CaptureFixture[str]) -> None:
    ran: list[bool] = []

    def run_setup() -> bool:
        ran.append(True)
        return True

    with pytest.raises(SystemExit) as excinfo:
        die_with_rescue(
            _fault(), debug=False, capable=True, confirm=lambda: False, run_setup=run_setup
        )
    assert excinfo.value.code == 2
    assert ran == []  # declined = never enters the flow
    err = capsys.readouterr().err
    assert err == screens.NO_MODEL + "\n"


def test_accept_runs_the_shared_flow_and_says_rerun(capsys: pytest.CaptureFixture[str]) -> None:
    said: list[str] = []
    with pytest.raises(SystemExit) as excinfo:
        die_with_rescue(
            _fault(),
            debug=False,
            capable=True,
            confirm=lambda: True,
            run_setup=lambda: True,
            say=said.append,
        )
    assert excinfo.value.code == 2  # the original command DID fail — rerun it
    assert said == [SAVED_RERUN]
    assert SAVED_RERUN == "saved - rerun your command"


def test_accept_without_a_save_stays_quiet(capsys: pytest.CaptureFixture[str]) -> None:
    said: list[str] = []
    with pytest.raises(SystemExit) as excinfo:
        die_with_rescue(
            _fault(),
            debug=False,
            capable=True,
            confirm=lambda: True,
            run_setup=lambda: False,  # cancelled inside the flow — nothing saved
            say=said.append,
        )
    assert excinfo.value.code == 2
    assert said == []


def test_other_setup_faults_never_get_the_offer(capsys: pytest.CaptureFixture[str]) -> None:
    asked: list[bool] = []

    def confirm() -> bool:
        asked.append(True)
        return True

    with pytest.raises(SystemExit) as excinfo:
        die_with_rescue(
            SetupFault("error: something else"), debug=False, capable=True, confirm=confirm
        )
    assert excinfo.value.code == 2
    assert asked == []


# --- the golden pin: the rescue screen variant --------------------------------------


def test_rescue_screen_variant_matches_golden() -> None:
    rendered = f"{screens.NO_MODEL}\n\n{RESCUE_PROMPT}\n"
    path = GOLDEN / "no_model_rescue.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        path.write_text(rendered, encoding="utf-8")
    assert rendered == path.read_text(encoding="utf-8"), (
        "the rescue screen drifted from its golden; if intended, run: make golden"
    )


# --- through the real stack: a verb with no model, no ollama, no TTY ------------------


def test_no_model_run_without_a_tty_is_the_plain_screen(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, run_cli: RunCli
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    monkeypatch.setenv("OLLAMA_HOST", "http://127.0.0.1:9")  # nothing listening
    for var in ("SMARTPIPE_MODEL", "OPENAI_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    code, out, err = run_cli(["map", "translate"], stdin="hello\n")
    assert code == 2
    assert out == ""
    assert err.startswith("error: no model configured")
    assert RESCUE_PROMPT not in err  # non-TTY never asks
