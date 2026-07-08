"""Shared verb options — file inputs (``--in``/``--from-files``) and ``--fields``."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

import click

from smartpipe.core.errors import UsageFault
from smartpipe.io.inputs import InputSpec

if TYPE_CHECKING:
    from collections.abc import Callable
    from pathlib import Path

__all__ = [
    "fields_option",
    "input_options",
    "input_spec",
    "parse_fields",
    "positional_paths",
    "resolve_prompt",
]

_Command = TypeVar("_Command", bound="Callable[..., object]")

_FIELDS_HINT = (
    "  --fields is a comma-separated list of columns, each named once.\n"
    "  Example: --fields name,email"
)


def input_options(command: _Command) -> _Command:
    """Attach the shared input dials: ``--in`` (glob of files as items),
    ``--from-files`` (stdin names files), and ``--as`` (granularity, item 15)."""
    command = click.option(
        "--in",
        "in_patterns",
        multiple=True,
        metavar="GLOB",
        hidden=True,  # compat alias (item 16): positional FILE arguments are the front door
        help="Read each matching file as one item (repeatable). e.g. --in 'docs/*.pdf'",
    )(command)
    command = click.option(
        "--from-files",
        "from_files",
        is_flag=True,
        help="Treat each stdin line as a filename; read each file as one item.",
    )(command)
    command = click.option(
        "--as",
        "as_mode",
        type=click.Choice(["file", "lines", "jsonl"]),
        default=None,
        help="Cut granularity: file = one item per file/whole stdin; "
        "lines = every line is text; jsonl = strict one-record-per-line.",
    )(command)
    return command


def input_spec(
    in_patterns: tuple[str, ...], *, from_files: bool, as_mode: str | None = None
) -> InputSpec:
    return InputSpec(patterns=tuple(in_patterns), from_files=from_files, as_mode=as_mode)


def positional_paths(paths: tuple[str, ...], in_patterns: tuple[str, ...]) -> tuple[str, ...]:
    """Positional FILE arguments (item 16): the same semantics as --in (which
    stays as a hidden compat alias). Two or more positionals that aren't on
    disk almost always mean an unquoted prompt — say so."""
    import glob as _glob
    from pathlib import Path as _Path

    missing = [path for path in paths if not _glob.has_magic(path) and not _Path(path).exists()]
    if len(missing) > 1:
        listed = ", ".join(missing[:3])
        raise UsageFault(
            f"{len(missing)} arguments aren't files on disk ({listed})\n"
            '  A multi-word prompt needs quotes: smartpipe map "summarize this" notes.txt'
        )
    return (*paths, *in_patterns)


def fields_option(command: _Command) -> _Command:
    """Attach ``--fields a,b`` — select + order the columns of structured output."""
    return click.option(
        "--fields",
        "fields",
        metavar="A,B,...",
        callback=_fields_callback,
        help="Select and order output columns (structured output only). e.g. --fields name,email",
    )(command)


def parse_fields(raw: str) -> tuple[str, ...]:
    """``" a , b "`` → ``("a", "b")``; empty or duplicate names are usage errors."""
    names = tuple(name.strip() for name in raw.split(","))
    if any(not name for name in names):
        raise UsageFault(f"--fields got an empty field name\n{_FIELDS_HINT}")
    seen: set[str] = set()
    for name in names:
        if name in seen:
            raise UsageFault(f"--fields names {name!r} more than once\n{_FIELDS_HINT}")
        seen.add(name)
    return names


def _fields_callback(
    ctx: click.Context, param: click.Parameter, value: str | None
) -> tuple[str, ...] | None:
    del ctx, param  # click's callback signature; the parse needs neither
    return None if value is None else parse_fields(value)


def resolve_prompt(argument: str | None, file_flag: Path | None) -> str:
    """D23: ``@file`` shorthand + ``--prompt-file`` — both spellings, one resolver.

    Only a LEADING ``@`` is special; ``@@x`` escapes a literal ``@``. Missing and
    empty files fail free at argv time (D18), before anything could cost money.
    """
    if argument is not None and file_flag is not None:
        raise UsageFault("a prompt argument and --prompt-file both given — use one")
    if file_flag is not None:
        return _read_prompt_file(file_flag)
    if argument is None:
        raise UsageFault("no prompt given — write one, or point at a file: --prompt-file FILE")
    if argument.startswith("@@"):
        return argument[1:]  # the escape: a literal leading @
    if argument.startswith("@"):
        from pathlib import Path as _Path

        return _read_prompt_file(_Path(argument[1:]))
    return argument


def _read_prompt_file(path: Path) -> str:
    if not path.exists():
        raise UsageFault(
            f"prompt file not found: {path}\n"
            "  @file reads the prompt from a file; --prompt-file FILE is the explicit form.\n"
            "  A literal leading @ escapes as @@."
        )
    text = path.read_text(encoding="utf-8").removesuffix("\n")
    if not text.strip():
        raise UsageFault(
            f"prompt file is empty: {path}\n"
            "  An empty prompt is never intended — write the prompt, or drop the @."
        )
    return text
