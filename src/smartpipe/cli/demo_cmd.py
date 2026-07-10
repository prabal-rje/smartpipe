"""``smartpipe demo`` - fetch the playground corpus. Zero model calls, zero config.

The flow lives in ``run_demo`` with every effect injected (the update_cmd DI
pattern); the network and archive work live in ``io/playground``, the
imperative shell. stdout carries exactly one thing - the copy-pasteable
next-steps block, because that block IS the command's result; every other
line (the consent prompt included) rides stderr.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import click

from smartpipe.cli import screens
from smartpipe.core.errors import SetupFault
from smartpipe.io import diagnostics, playground

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = ["demo_command", "fetch_playground", "run_demo", "stdin_is_tty"]


@click.command(name="demo")
def demo_command() -> None:
    """Download the playground corpus - practice files, no model needed.

    \b
    Examples:
      smartpipe demo        ~26 MB into ./smartpipe-playground, then next steps

    Fetches 26 MB of CC0 / public-domain practice files - scanned invoices,
    NASA reports, photos, speech recordings, screen sessions, JSONL data -
    into ./smartpipe-playground and prints commands to try on them. Asks
    first at a terminal (Enter continues); pipes and scripts proceed. The
    download is checksum-verified, and an existing complete download is
    recognized and left alone - smartpipe demo never overwrites anything.
    """
    from pathlib import Path as _Path

    run_demo(
        target=_Path.cwd() / playground.PLAYGROUND_DIR,
        is_tty=stdin_is_tty(),
        confirm=_confirm,
        fetch=fetch_playground,
        expected_sha256=playground.PLAYGROUND_SHA256,
        say=click.echo,
        tell=diagnostics.note,
    )


def run_demo(
    *,
    target: Path,
    is_tty: bool,
    confirm: Callable[[str], bool],
    fetch: Callable[[], bytes],
    expected_sha256: str,
    say: Callable[[str], None],
    tell: Callable[[str], None],
) -> None:
    """The whole flow with its effects injected - the update_cmd DI pattern.

    ``say`` is the result channel (stdout), ``tell`` the diagnostic one
    (stderr). Exit codes: 0 for done / already here / declined; the in-the-way
    and wire/digest failures raise ``SetupFault`` (exit 2).
    """
    if target.is_dir() and any(target.iterdir()):
        if playground.looks_complete(entry.name for entry in target.iterdir()):
            say(screens.DEMO_ALREADY_HERE)
            return
        raise SetupFault(screens.DEMO_DIR_IN_THE_WAY)
    if target.exists() and not target.is_dir():
        raise SetupFault(screens.DEMO_DIR_IN_THE_WAY)
    if is_tty and not confirm(screens.DEMO_CONFIRM):
        tell("demo declined - nothing downloaded")
        return
    tell(f"downloading {playground.PLAYGROUND_URL} ({playground.PLAYGROUND_SIZE_LABEL})")
    data = fetch()
    playground.verify(data, expected_sha256=expected_sha256)
    tell("sha256 verified - unpacking")
    playground.unpack(data, target)
    say(screens.DEMO_READY)


def stdin_is_tty() -> bool:
    """The real-process boundary the consent prompt gates on: an interactive
    stdin asks; a script or pipe opted in by invoking the command."""
    import sys

    return sys.stdin.isatty()


def fetch_playground() -> bytes:
    """The network boundary: stream the pinned release asset (io/playground)."""
    return playground.fetch_corpus(playground.PLAYGROUND_URL)


def _confirm(question: str) -> bool:
    """Enter continues (yes is the default); Ctrl+C/EOF at the prompt declines.
    The prompt rides stderr - stdout carries nothing but the result."""
    try:
        return click.confirm(question, default=True, err=True)
    except click.Abort:
        return False
