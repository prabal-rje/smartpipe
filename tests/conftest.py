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
    # …nor the developer's real key store (the auth-login wave): every test
    # resolves the data-dir auth.json inside its own tmp_path.
    monkeypatch.setenv("XDG_DATA_HOME", str(tmp_path / "data"))
    monkeypatch.setenv("LOCALAPPDATA", str(tmp_path / "data"))  # the windows twin
    # …nor the developer's real config.toml (live-caught 2026-07-10: the
    # owner's test-drive stamped ocr-model and 7 embed/top_k tests started
    # resolving a Mistral parser locally while CI stayed green - CI runners
    # simply HAVE no config; this pin makes local runs match CI)
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path / "config"))
    monkeypatch.setenv("APPDATA", str(tmp_path / "config"))  # the windows twin (D09)
    # …and no ambient provider credentials (item 78, live-caught 2026-07-10:
    # magika - markitdown's sniffer - load_dotenv()'d the repo's .env into the
    # process mid-suite, and the auth-list tests started seeing real keys on
    # whichever xdist worker ran a document test first; the product-side
    # environ_fence fixes the ingestion, this pin makes key-sensitive tests
    # deterministic under ANY ambient environment, exported keys included)
    from smartpipe.config.credentials import KEY_ENVS

    for env_vars in KEY_ENVS.values():
        for name in env_vars:
            monkeypatch.delenv(name, raising=False)
    # …nor an ambient --local-only fence (item 65d): the flag exports
    # SMARTPIPE_LOCAL_ONLY into os.environ, so the delenv both isolates tests
    # from the developer's shell and undoes any in-process export at teardown
    monkeypatch.delenv("SMARTPIPE_LOCAL_ONLY", raising=False)
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
        # main() leaves the SIGPIPE disposition alone since item 75 (SIG_IGN,
        # Python's default) — no restore needed; test_signals.py pins it.
        captured = capsys.readouterr()
        return code, captured.out, captured.err

    return _run
