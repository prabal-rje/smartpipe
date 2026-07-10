"""The NO_MODEL rescue wizard (item 50).

When the NO_MODEL SetupFault reaches the exit path at a REAL terminal
(stdin+stdout are TTYs, TERM is capable, not CI), the screen prints exactly as
always, then ONE question follows — ``run setup now? [Y/n]`` — and Enter/y
drops into the shared staged flow (``open_setup_flow``). Decline, or any
non-terminal context, is byte-identical to today's behavior: the plain
screen, exit 2. The original command still failed, so the rescue EXITS 2
either way — the closing ``saved - rerun your command`` line says so plainly.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, NoReturn

from smartpipe.core.errors import ExitCode, SetupFault

if TYPE_CHECKING:
    from collections.abc import Callable, Mapping

__all__ = ["RESCUE_PROMPT", "SAVED_RERUN", "die_with_rescue", "rescue_capable"]

_CI_VARS = ("CI", "GITHUB_ACTIONS")  # the update check's fence, reused
_QUESTION = "run setup now?"
RESCUE_PROMPT = f"{_QUESTION} [Y/n]"  # how click.confirm(default=True) renders it
SAVED_RERUN = "saved - rerun your command"


def rescue_capable(env: Mapping[str, str], *, stdin_tty: bool, stdout_tty: bool) -> bool:
    """A REAL terminal only: both ends TTYs, a capable TERM, and not CI."""
    if not (stdin_tty and stdout_tty):
        return False
    if env.get("TERM") == "dumb":
        return False
    return not any(env.get(var, "").strip() for var in _CI_VARS)


def die_with_rescue(
    fault: SetupFault,
    *,
    debug: bool,
    capable: bool | None = None,
    confirm: Callable[[], bool] | None = None,
    run_setup: Callable[[], bool] | None = None,
    say: Callable[[str], None] | None = None,
) -> NoReturn:
    """Exit on a SetupFault; for the NO_MODEL screen at a real TTY, offer setup first.

    The injectable callables (``capable``/``confirm``/``run_setup``/``say``)
    default to the real terminal wiring — tests hand in fakes.
    """
    from smartpipe.cli import screens
    from smartpipe.io import diagnostics

    if str(fault) != screens.NO_MODEL:
        diagnostics.die(fault, debug=debug)
    if capable is None:
        capable = _real_capable()
    if not capable:
        diagnostics.die(fault, debug=debug)  # byte-identical to today: screen, exit 2
    diagnostics.report_error(str(fault))
    if (confirm if confirm is not None else _real_confirm)():
        saved = (run_setup if run_setup is not None else _real_setup)()
        if saved:
            (say if say is not None else _say_stderr)(SAVED_RERUN)
    raise SystemExit(int(ExitCode.SETUP))


def _real_capable() -> bool:  # pragma: no cover — process state; the gate itself is tested
    import os
    import sys

    return rescue_capable(os.environ, stdin_tty=sys.stdin.isatty(), stdout_tty=sys.stdout.isatty())


def _real_confirm() -> bool:  # pragma: no cover — terminal wiring
    import click

    return bool(click.confirm(_QUESTION, default=True, err=True))


def _real_setup() -> bool:  # pragma: no cover — terminal + network wiring
    import asyncio

    from smartpipe.cli.config_cmd import open_setup_flow

    return asyncio.run(open_setup_flow(stamped_by="smartpipe use"))


def _say_stderr(message: str) -> None:
    import sys

    sys.stderr.write(f"{message}\n")
    sys.stderr.flush()
