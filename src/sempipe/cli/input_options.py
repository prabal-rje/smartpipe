"""Shared ``--in`` / ``--from-files`` options, so every verb reads files the same way."""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeVar

import click

from sempipe.io.inputs import InputSpec

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["input_options", "input_spec"]

_Command = TypeVar("_Command", bound="Callable[..., object]")


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
