"""The smartpipe entry point: root command group and exit-code mapping.

click's built-in usage-error exit code (2) collides with the spec's "no model
configured" (plan/decisions.md D12), so ``main`` maps click exceptions onto the
``ExitCode`` contract itself instead of using click's standalone mode.
"""

from __future__ import annotations

import asyncio
import contextlib
import os
import sys
from typing import TYPE_CHECKING

import click

from smartpipe import __version__
from smartpipe.cli.agree_cmd import agree_command
from smartpipe.cli.auth_cmd import auth_command
from smartpipe.cli.cache_cmd import cache_command
from smartpipe.cli.chart_cmd import chart_command
from smartpipe.cli.cite_cmd import cite_command
from smartpipe.cli.cluster_cmd import cluster_command
from smartpipe.cli.config_cmd import config_command, use_command, using_command
from smartpipe.cli.diff_cmd import diff_command
from smartpipe.cli.distinct_cmd import distinct_command
from smartpipe.cli.doctor_cmd import doctor_command
from smartpipe.cli.echo_cmd import echo_command
from smartpipe.cli.embed_cmd import embed_command
from smartpipe.cli.extend_cmd import extend_command
from smartpipe.cli.filter_cmd import filter_command
from smartpipe.cli.getschema_cmd import getschema_command
from smartpipe.cli.graph_cmd import graph_command
from smartpipe.cli.join_cmd import join_command
from smartpipe.cli.map_cmd import map_command
from smartpipe.cli.outliers_cmd import outliers_command
from smartpipe.cli.readable_cmd import readable_command
from smartpipe.cli.reduce_cmd import reduce_command
from smartpipe.cli.run_cmd import run_command
from smartpipe.cli.sample_cmd import sample_command
from smartpipe.cli.schema_cmd import schema_command
from smartpipe.cli.screens import WELCOME
from smartpipe.cli.sort_cmd import sort_command
from smartpipe.cli.split_cmd import split_command
from smartpipe.cli.summarize_cmd import summarize_command
from smartpipe.cli.top_k_cmd import top_k_command
from smartpipe.cli.update_cmd import update_command
from smartpipe.cli.usage_cmd import usage_command
from smartpipe.cli.where_cmd import where_command
from smartpipe.cli.write_cmd import write_command
from smartpipe.core.errors import ExitCode, SempipeError, SetupFault, UsageFault

if True:  # typing-only import kept runtime-cheap
    from collections.abc import Iterable
from smartpipe.io import diagnostics

if TYPE_CHECKING:
    from pathlib import Path

__all__ = ["cli", "main"]


_ALIASES = {"top-k": "top_k", "topk": "top_k"}


class _StyledHelpFormatter(click.HelpFormatter):
    """Color in --help (D42): headings cyan, commands/options green. ANSI is
    stripped by click when piped; NO_COLOR turns it off entirely."""

    @staticmethod
    def _on() -> bool:
        return sys.stdout.isatty() and not os.environ.get("NO_COLOR")

    def write_usage(self, prog: str, args: str = "", prefix: str | None = None) -> None:
        if self._on():
            prog = click.style(prog, bold=True)
            prefix = click.style(prefix if prefix is not None else "Usage: ", fg="cyan", bold=True)
        super().write_usage(prog, args, prefix)

    def write_heading(self, heading: str) -> None:
        if self._on():
            heading = click.style(heading, fg="cyan", bold=True)
        super().write_heading(heading)

    def write_dl(
        self,
        rows: Iterable[tuple[str, str]],
        col_max: int = 30,
        col_spacing: int = 2,
    ) -> None:
        if self._on():
            rows = [(click.style(term, fg="green"), body) for term, body in rows]
        super().write_dl(rows, col_max, col_spacing)


class _StyledContext(click.Context):
    formatter_class = _StyledHelpFormatter


class _PipeClosedError(RuntimeError):
    """A ``BrokenPipeError`` smuggled past click (item 75): click's own EPIPE
    handler turns any ``OSError(EPIPE)`` into ``sys.exit(1)`` — even in
    non-standalone mode — before ``main`` could map it to the pinned 141.
    ``_RootGroup.invoke`` re-raises it as this non-OSError instead."""


class _RootGroup(click.Group):
    """Print the welcome screen when invoked bare (plan/ux.md, spec §14)."""

    def invoke(self, ctx: click.Context) -> object:
        try:
            return super().invoke(ctx)
        except BrokenPipeError as exc:
            # downstream closed mid-verb (| head) — shield the EPIPE from
            # click's exit-1 trap; main() maps it to the quiet 141
            raise _PipeClosedError from exc

    def parse_args(self, ctx: click.Context, args: list[str]) -> list[str]:
        if not args:
            plain = bool(os.environ.get("NO_COLOR"))
            click.echo(WELCOME, nl=False, color=False if plain else None)
            ctx.exit(int(ExitCode.OK))
        return super().parse_args(ctx, args)

    def resolve_command(
        self, ctx: click.Context, args: list[str]
    ) -> tuple[str | None, click.Command | None, list[str]]:
        # Reader mode (item 16): a first argument that is no verb but exists on
        # disk makes the binary the reader — `smartpipe report.pdf` emits items.
        # Verbs always win names; `./name` forces the file when one shadows a verb.
        # A quoted glob (`smartpipe 'logs/*.jsonl'`) is a reader too: the reader
        # expands its own patterns (D43); a spaceless token with glob chars can
        # only be a pattern, never a prompt.
        head = args[0]
        if not head.startswith("-") and self.get_command(ctx, head) is None:
            from pathlib import Path as _Path

            looks_like_glob = " " not in head and any(char in head for char in "*?[")
            if head.startswith(("./", "../", "/")) or looks_like_glob or _Path(head).exists():
                from smartpipe.cli.read_cmd import read_command

                return read_command.name, read_command, args
            raise click.UsageError(
                f"no verb '{head}', no file '{head}' — typo, or quote your prompt?"
            )
        return super().resolve_command(ctx, args)

    def get_command(self, ctx: click.Context, cmd_name: str) -> click.Command | None:
        # Muscle-memory forgiveness for top_k's spelling; not shown in help.
        built_in = super().get_command(ctx, _ALIASES.get(cmd_name, cmd_name))
        if built_in is not None:
            return built_in  # built-ins always win — custom verbs never shadow
        return _user_sem_command(cmd_name) or _entry_point_command(cmd_name)

    def list_commands(self, ctx: click.Context) -> list[str]:
        names = set(super().list_commands(ctx))
        names.update(_user_sem_names())
        names.update(_entry_point_names())
        return sorted(names)


def _verbs_dir() -> Path:
    from smartpipe.config.paths import config_path

    return config_path(os.environ).parent / "verbs"


def _user_sem_names() -> list[str]:
    try:
        return [path.stem for path in _verbs_dir().glob("*.sem")]
    except OSError:
        return []


def _user_sem_command(name: str) -> click.Command | None:
    """Leg 1 of the custom-verb contract (D39/06): ~/.config/smartpipe/verbs/
    NAME.sem becomes `smartpipe NAME` — full key validation, zero machinery."""
    script = _verbs_dir() / f"{name}.sem"
    if not script.is_file():
        return None

    @click.command(name=name, help=f"Custom verb from {script} (.sem file).")
    def sem_verb() -> None:
        from smartpipe.cli.run_cmd import execute_script

        execute_script(script)

    return sem_verb


def _entry_point_names() -> list[str]:
    from importlib.metadata import entry_points

    try:
        return [point.name for point in entry_points(group="smartpipe.verbs")]
    except Exception:
        return []


def _entry_point_command(name: str) -> click.Command | None:
    """Leg 2 (D39/06): a package's entry point in group `smartpipe.verbs` naming
    a click.Command. Broken plugins warn once and are skipped."""
    from importlib.metadata import entry_points

    try:
        matches = [point for point in entry_points(group="smartpipe.verbs") if point.name == name]
    except Exception:
        return None
    for point in matches:
        try:
            loaded: object = point.load()
        except Exception as exc:
            diagnostics.warn(f"custom verb {name!r} failed to load ({exc}) — skipped")
            return None
        if isinstance(loaded, click.Command):
            return loaded
        diagnostics.warn(f"custom verb {name!r} is not a click.Command — skipped")
    return None


@click.group(cls=_RootGroup)
@click.version_option(__version__, prog_name="smartpipe", message="%(prog)s %(version)s")
@click.option(
    "--local-only",
    "local_only_flag",
    is_flag=True,
    help="Hard privacy fence: refuse every cloud wire; the run makes no network "
    "calls at all (env form: SMARTPIPE_LOCAL_ONLY=1).",
)
def cli(local_only_flag: bool) -> None:
    """smartpipe — semantic pipes for your terminal: documents, images, audio, video, text."""
    if local_only_flag:
        # the flag becomes the env form so ONE predicate (core/fence.local_only)
        # governs the container, the update ping, and the catalog fetches
        os.environ["SMARTPIPE_LOCAL_ONLY"] = "1"


cli.add_command(map_command)
cli.add_command(extend_command)
cli.add_command(filter_command)
cli.add_command(embed_command)
cli.add_command(top_k_command)
cli.add_command(reduce_command)
cli.add_command(join_command)
cli.add_command(cluster_command)
cli.add_command(graph_command)
cli.add_command(diff_command)
cli.add_command(distinct_command)
cli.add_command(outliers_command)
cli.add_command(run_command)
cli.add_command(config_command)
cli.add_command(use_command)
cli.add_command(using_command)
cli.add_command(doctor_command)
cli.add_command(schema_command)
cli.add_command(split_command)
cli.add_command(chart_command)
cli.add_command(where_command)
cli.add_command(write_command)
cli.add_command(readable_command)
cli.add_command(summarize_command)
cli.add_command(sample_command)
cli.add_command(agree_command)
cli.add_command(getschema_command)
cli.add_command(sort_command)
cli.add_command(auth_command)
cli.add_command(cache_command)
cli.add_command(update_command)
cli.add_command(usage_command)
cli.add_command(cite_command)
cli.add_command(echo_command)


def _stylize(command: click.Command) -> None:
    command.context_class = _StyledContext
    if isinstance(command, click.Group):
        for sub in command.commands.values():
            _stylize(sub)


_stylize(cli)


def main() -> None:
    # standalone_mode=False so *we* own exit codes. In this mode click does not
    # sys.exit(): a ctx.exit(n) / --version / --help comes back as a plain int
    # return value (verified against click 8.4), and UsageError is raised.
    # --debug becomes a real global flag with the first verb (stage 3); until then
    # the env var keeps tracebacks reachable for development.
    #
    # SIGPIPE stays IGNORED (Python's default) on purpose — never SIG_DFL
    # (item 75). Process-wide SIG_DFL turned every stray EPIPE into raw
    # signal death (-13): a provider socket, or the event loop's self-pipe
    # when the stdin pump's call_soon_threadsafe races loop teardown (the
    # rc3 1-in-12 streaming flake; measured 5% locally on a flowing stdin).
    # With SIG_IGN a closed downstream pipe surfaces as BrokenPipeError,
    # converted below to the pinned quiet exit 141 — and the run's finally
    # blocks (usage ledger, receipts, spool cleanup) still get to run.
    debug = "SMARTPIPE_DEBUG" in os.environ
    # notify-next-run (npm-style): a daemon thread refreshes the release cache
    # while the command works; the note prints from the CACHED answer at the
    # end, so the check adds zero latency. Both hooks are self-gating (TTY,
    # CI, kill switches) and swallow every failure.
    from smartpipe.io import update_check

    # --local-only is parsed AFTER this hook would fire, so the flag is
    # pre-scanned here; the env form is caught by check_allowed itself (65d)
    if "--local-only" not in sys.argv[1:]:
        update_check.begin_background_check()
    try:
        result = cli.main(
            standalone_mode=False,
            prog_name="smartpipe",
            # click glob-expands wildcard args itself on windows - that would
            # pre-explode a quoted --in '*.txt' into positionals and break the
            # "we expand our own globs" contract (D43-era; caught by CI round 10)
            windows_expand_args=False,
        )
    except (BrokenPipeError, _PipeClosedError):  # downstream closed (| head): grep-like quiet 141
        with contextlib.suppress(OSError, ValueError):  # silence the shutdown flush
            os.dup2(os.open(os.devnull, os.O_WRONLY), sys.stdout.fileno())
        raise SystemExit(int(ExitCode.PIPE_CLOSED)) from None
    except click.UsageError as exc:
        prefix = (
            click.style("error:", fg="red")
            if sys.stderr.isatty() and not os.environ.get("NO_COLOR")
            else "error:"
        )
        click.echo(f"{prefix} {exc.format_message()}", err=True)
        command_path = exc.ctx.command_path if exc.ctx is not None else "smartpipe"
        click.echo(f"  try: {command_path} --help", err=True)
        raise SystemExit(int(ExitCode.USAGE)) from exc
    except SempipeError as exc:
        if isinstance(exc, SetupFault):
            # the NO_MODEL screen at a real terminal offers the setup wizard
            # (item 50); every other fault — and every non-TTY context — is
            # byte-identical to diagnostics.die
            from smartpipe.cli.rescue import die_with_rescue

            die_with_rescue(exc, debug=debug)
        diagnostics.die(exc, debug=debug)
    except (KeyboardInterrupt, click.Abort):
        raise SystemExit(int(ExitCode.INTERRUPTED)) from None
    except asyncio.CancelledError:  # the drain watchdog cancelled the run (ux.md §12)
        raise SystemExit(int(ExitCode.INTERRUPTED)) from None
    except click.ClickException as exc:  # click-internal faults (e.g. click.FileError)
        diagnostics.die(UsageFault(exc.format_message()), debug=debug)
    except Exception as exc:  # the last-resort BUG screen (exit 70) — never a raw traceback
        diagnostics.internal_error(exc, debug=debug)
    if sys.argv[1:2] != ["update"]:  # right after updating, the nag would be noise
        update_check.emit_update_notice(__version__)
    if isinstance(result, int) and result != 0:
        raise SystemExit(result)
