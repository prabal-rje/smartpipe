"""Shared fixtures.

``run_cli`` is the canonical way every test invokes sempipe: it exercises the real
``main()`` entry point (including its exception-to-exit-code mapping, which click's
``CliRunner`` would bypass) while capturing stdout/stderr separately.
"""

from __future__ import annotations

import io
from typing import TYPE_CHECKING, Protocol

import pytest

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = ["RunCli", "run_cli"]


class RunCli(Protocol):
    def __call__(self, args: Sequence[str], stdin: str | None = None) -> tuple[int, str, str]: ...


@pytest.fixture
def run_cli(monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]) -> RunCli:
    def _run(args: Sequence[str], stdin: str | None = None) -> tuple[int, str, str]:
        from sempipe.cli.root import main

        monkeypatch.setattr("sys.argv", ["sempipe", *args])
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
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run
