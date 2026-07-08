"""``smartpipe update`` — upgrade through the tool that installed smartpipe.

Channel detection is a pure function over ``sys.executable`` and the package
path (``core/install_channel``); the upgrade tool runs attached to this
terminal so its own output tells the story. smartpipe adds exactly three
things: what it detected, a consent prompt (y/N, skipped by ``--yes``), and
an honest exit code — the tool failing is a setup fault (exit 2), an
unrecognized channel is guidance (exit 0), a declined prompt changes nothing.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from smartpipe import __version__
from smartpipe.cli import screens
from smartpipe.core.errors import SetupFault
from smartpipe.core.install_channel import Channel, detect_channel, upgrade_command

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence

__all__ = ["execute_upgrade", "install_paths", "run_update", "update_command"]


@click.command(name="update")
@click.option("--yes", is_flag=True, help="Skip the confirmation prompt.")
def update_command(yes: bool) -> None:
    """Update smartpipe to the latest release.

    \b
    Examples:
      smartpipe update          show the plan, ask, then upgrade
      smartpipe update --yes    upgrade without the prompt (scripts)

    Detects how smartpipe was installed — Homebrew, uv tool, pipx, or pip —
    and runs that tool's own upgrade command, after showing it and asking.
    An unrecognized install prints the per-channel commands and changes
    nothing.
    """
    executable, module_path = install_paths()
    run_update(
        channel=detect_channel(executable, module_path),
        version=__version__,
        assume_yes=yes,
        confirm=_confirm,
        say=click.echo,
        execute=execute_upgrade,
    )


def run_update(
    *,
    channel: Channel,
    version: str,
    assume_yes: bool,
    confirm: Callable[[str], bool],
    say: Callable[[str], None],
    execute: Callable[[Sequence[str]], int],
) -> None:
    """The whole flow with its I/O injected — the config-wizard DI pattern."""
    command = upgrade_command(channel)
    if command is None:
        say(screens.update_unknown_channel(version))
        return
    rendered = " ".join(command)
    say(screens.update_plan(str(channel), rendered, version))
    if not (assume_yes or confirm("Proceed?")):
        say("update declined — nothing changed")
        return
    exit_code = execute(command)
    if exit_code != 0:
        raise SetupFault(screens.update_failed(rendered, exit_code))
    say(screens.update_done(rendered))


def install_paths() -> tuple[str, str]:
    """The two real-process strings detection reads (the patchable boundary)."""
    import sys

    import smartpipe

    return sys.executable, str(smartpipe.__file__)


def _confirm(question: str) -> bool:
    """y/N with decline as the default — Ctrl+C/EOF at the prompt declines too."""
    try:
        return click.confirm(question, default=False)
    except click.Abort:
        return False


def execute_upgrade(argv: Sequence[str]) -> int:
    """Run the upgrade tool attached to this terminal; its output is the story."""
    import subprocess

    try:
        return subprocess.run(list(argv), check=False).returncode
    except OSError as exc:
        raise SetupFault(screens.update_tool_missing(argv[0], " ".join(argv))) from exc
