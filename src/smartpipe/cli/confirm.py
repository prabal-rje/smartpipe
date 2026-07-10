"""A yes/no prompt that provably never touches stdout.

``click.confirm(err=True)`` writes the prompt to stderr but hands its
separator to ``input()``, which writes to STDOUT on Windows (matrix-caught
2026-07-10: a piped ``smartpipe demo`` on Windows prepended a space to the
result stream). ``input()`` with no argument writes nothing anywhere - so
we echo the whole prompt to stderr ourselves and read bare.
"""

from __future__ import annotations

import click

__all__ = ["confirm_on_stderr"]


def confirm_on_stderr(question: str, *, default: bool = True) -> bool:
    """Render ``question [Y/n]: `` on stderr and read the answer.

    Enter takes the default; Ctrl+C/EOF declines. Rendering matches what
    ``click.confirm`` produced, so the pinned prompts are byte-identical.
    """
    suffix = "[Y/n]" if default else "[y/N]"
    click.echo(f"{question} {suffix}: ", nl=False, err=True)
    try:
        answer = input()
    except (EOFError, KeyboardInterrupt):
        click.echo("", err=True)  # land the cursor like an answered prompt
        return False
    answer = answer.strip().lower()
    if not answer:
        return default
    return answer in ("y", "yes")
