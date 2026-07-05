"""Shared verb options — file inputs (``--in``/``--from-files``) and ``--fields``."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

import click

from sempipe.core.errors import UsageFault
from sempipe.io.inputs import InputSpec

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["fields_option", "input_options", "input_spec", "parse_fields"]

_Command = TypeVar("_Command", bound="Callable[..., object]")

_FIELDS_HINT = (
    "  --fields is a comma-separated list of columns, each named once.\n"
    "  Example: --fields name,email"
)


def input_options(command: _Command) -> _Command:
    """Attach ``--in`` (glob of files as items) and ``--from-files`` (stdin names files)."""
    command = click.option(
        "--in",
        "in_patterns",
        multiple=True,
        metavar="GLOB",
        help="Read each matching file as one item (repeatable). e.g. --in 'docs/*.pdf'",
    )(command)
    command = click.option(
        "--from-files",
        "from_files",
        is_flag=True,
        help="Treat each stdin line as a filename; read each file as one item.",
    )(command)
    return command


def input_spec(in_patterns: tuple[str, ...], *, from_files: bool) -> InputSpec:
    return InputSpec(patterns=tuple(in_patterns), from_files=from_files)


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
