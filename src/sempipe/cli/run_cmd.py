"""``sempipe run`` — execute a ``.sem`` stage file (D17).

A thin trampoline: translate the file to argv, append any extra flags (click's
last-wins makes the CLI override the file), and invoke the verb in a fresh
sub-context. Faults propagate to ``main()``'s single exit-code mapping — this
command owns no error handling of its own.
"""

from __future__ import annotations

from pathlib import Path

import click

from sempipe.cli.sem_file import parse_sem

__all__ = ["run_command"]


@click.command(name="run", context_settings={"ignore_unknown_options": True})
@click.argument("script", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def run_command(script: Path, extra: tuple[str, ...]) -> None:
    """Execute a .sem stage file. Extra flags override the file's values.

    \b
    Examples:
      sempipe run extract.sem < cards.txt
      sempipe run extract.sem --model ollama/qwen3:8b
      cat tickets.log | ./filter-urgent.sem | ./extract.sem

    A .sem file is TOML pinning one verb invocation; start it with
    '#!/usr/bin/env -S sempipe run' and chmod +x to run it directly.
    stdin and stdout flow exactly as if the command had been typed.
    """
    argv = [*parse_sem(script), *extra]
    context = click.get_current_context()
    root = context.find_root().command
    assert isinstance(root, click.Group)  # run is only ever registered on the group
    verb = root.get_command(context, argv[0])
    assert verb is not None  # the translator only emits real verbs
    # a fresh sub-context, NOT inheriting ignore_unknown_options — a typo'd extra
    # flag must still be a loud usage error inside the verb
    sub = verb.make_context(argv[0], list(argv[1:]), parent=context, ignore_unknown_options=False)
    with sub:
        verb.invoke(sub)
