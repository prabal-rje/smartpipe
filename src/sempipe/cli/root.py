"""The sempipe entry point: root command group and exit-code mapping.

click's built-in usage-error exit code (2) collides with the spec's "no model
configured" (plan/decisions.md D12), so ``main`` maps click exceptions onto the
``ExitCode`` contract itself instead of using click's standalone mode.
"""

from __future__ import annotations

import click

from sempipe import __version__
from sempipe.cli.screens import WELCOME
from sempipe.core.errors import ExitCode

__all__ = ["cli", "main"]


class _RootGroup(click.Group):
    """Print the welcome screen when invoked bare (plan/ux.md, spec §14)."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args:
            click.echo(WELCOME, nl=False)
            ctx.exit(int(ExitCode.OK))
        return super().parse_args(ctx, args)


@click.group(cls=_RootGroup)
@click.version_option(__version__, prog_name="sempipe", message="%(prog)s %(version)s")
def cli() -> None:
    """sempipe — semantic pipes for your terminal."""


def main() -> None:
    try:
        cli.main(standalone_mode=False)
    except click.UsageError as exc:
        click.echo(f"error: {exc.format_message()}", err=True)
        command_path = exc.ctx.command_path if exc.ctx is not None else "sempipe"
        click.echo(f"  try: {command_path} --help", err=True)
        raise SystemExit(int(ExitCode.USAGE)) from exc
    except click.exceptions.Exit as exc:
        raise SystemExit(exc.exit_code) from exc
    except click.ClickException as exc:
        exc.show()
        raise SystemExit(int(ExitCode.USAGE)) from exc
