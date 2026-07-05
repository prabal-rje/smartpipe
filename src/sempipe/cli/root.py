"""The sempipe entry point: root command group and exit-code mapping.

click's built-in usage-error exit code (2) collides with the spec's "no model
configured" (plan/decisions.md D12), so ``main`` maps click exceptions onto the
``ExitCode`` contract itself instead of using click's standalone mode.
"""

from __future__ import annotations

import os

import click

from sempipe import __version__
from sempipe.cli.config_cmd import config_command
from sempipe.cli.echo_cmd import echo_command
from sempipe.cli.embed_cmd import embed_command
from sempipe.cli.filter_cmd import filter_command
from sempipe.cli.map_cmd import map_command
from sempipe.cli.screens import WELCOME
from sempipe.cli.top_k_cmd import top_k_command
from sempipe.core.errors import ExitCode, SempipeError, UsageFault
from sempipe.io import diagnostics

__all__ = ["cli", "main"]


_ALIASES = {"top-k": "top_k", "topk": "top_k"}


class _RootGroup(click.Group):
    """Print the welcome screen when invoked bare (plan/ux.md, spec §14)."""

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args:
            click.echo(WELCOME, nl=False)
            ctx.exit(int(ExitCode.OK))
        return super().parse_args(ctx, args)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        # Muscle-memory forgiveness for top_k's spelling; not shown in help.
        return super().get_command(ctx, _ALIASES.get(cmd_name, cmd_name))


@click.group(cls=_RootGroup)
@click.version_option(__version__, prog_name="sempipe", message="%(prog)s %(version)s")
def cli() -> None:
    """sempipe — semantic pipes for your terminal."""


cli.add_command(map_command)
cli.add_command(filter_command)
cli.add_command(embed_command)
cli.add_command(top_k_command)
cli.add_command(config_command)
cli.add_command(echo_command)


def main() -> None:
    # standalone_mode=False so *we* own exit codes. In this mode click does not
    # sys.exit(): a ctx.exit(n) / --version / --help comes back as a plain int
    # return value (verified against click 8.4), and UsageError is raised.
    # --debug becomes a real global flag with the first verb (stage 3); until then
    # the env var keeps tracebacks reachable for development.
    debug = "SEMPIPE_DEBUG" in os.environ
    try:
        result = cli.main(standalone_mode=False, prog_name="sempipe")
    except click.UsageError as exc:
        click.echo(f"error: {exc.format_message()}", err=True)
        command_path = exc.ctx.command_path if exc.ctx is not None else "sempipe"
        click.echo(f"  try: {command_path} --help", err=True)
        raise SystemExit(int(ExitCode.USAGE)) from exc
    except SempipeError as exc:
        diagnostics.die(exc, debug=debug)
    except KeyboardInterrupt:
        raise SystemExit(int(ExitCode.INTERRUPTED)) from None
    except click.ClickException as exc:  # click-internal faults (e.g. click.FileError)
        diagnostics.die(UsageFault(exc.format_message()), debug=debug)
    except Exception as exc:  # the last-resort BUG screen (exit 70) — never a raw traceback
        diagnostics.internal_error(exc, debug=debug)
    if isinstance(result, int) and result != 0:
        raise SystemExit(result)
