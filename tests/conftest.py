"""Shared fixtures.

``run_cli`` is the canonical way every test invokes smartpipe: it exercises the real
``main()`` entry point (including its exception-to-exit-code mapping, which click's
``CliRunner`` would bypass) while capturing stdout/stderr separately.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["RunCli", "run_cli"]


class RunCli(Protocol):
    def __call__(self, args: Sequence[str], stdin: str | None = None) -> tuple[int, str, str]: ...


@pytest.fixture(autouse=True)
def isolated_state(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    """No test may touch the developer's real ledger/cache state (D41 —
    live-caught: respx runs wrote to the real ~/.local/state), and every
    test starts with a fresh meter."""
    from smartpipe.io import metering

    monkeypatch.setenv("XDG_STATE_HOME", str(tmp_path / "state"))
    metering.reset()


@pytest.fixture
def run_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> RunCli:
    def _run(args: Sequence[str], stdin: str | None = None) -> tuple[int, str, str]:
        from smartpipe.cli.root import main

        monkeypatch.setattr("sys.argv", ["smartpipe", *args])
        if stdin is not None:
            monkeypatch.setattr("sys.stdin", io.StringIO(stdin))
        code = 0
        try:
            main()
        except SystemExit as exc:
            match exc.code:
                case int() as value:
                    code = value
                case None:
                    code = 0
                case _:
                    code = 1
        finally:
            # main() sets SIGPIPE to SIG_DFL process-wide (the grep-like death
            # contract). Correct for the real CLI; lethal inside pytest — any
            # later closed-pipe write would kill the whole run with 141 (seen
            # on the Linux CI runner). Restore Python's default after each run.
            import signal as _signal

            if hasattr(_signal, "SIGPIPE"):
                _signal.signal(_signal.SIGPIPE, _signal.SIG_IGN)
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run
