"""``smartpipe run`` — execute a ``.sem`` file: one stage, or a pipeline (D17/D38-14).

Single-stage files trampoline into their verb unchanged. Pipeline files
([stage.NAME] tables) run stages sequentially in-process: each stage's stdout
feeds the next (or a named earlier stage), the last stage writes the real
stdout, and every stage's stderr is prefixed with its name. ``--dry-run``
prints the resolved graph with each stage's cost posture and runs nothing —
D18 at pipeline scale.
"""

from __future__ import annotations

import contextlib
import io
import os
import sys
from pathlib import Path
from typing import TYPE_CHECKING

import click

from smartpipe.cli.sem_file import parse_pipeline, parse_single
from smartpipe.io import progress

if TYPE_CHECKING:
    from collections.abc import Generator

    from smartpipe.cli.sem_file import Stage

__all__ = ["execute_script", "run_command"]

_FREE_VERBS = frozenset({"where", "split", "chart", "sort", "sample", "summarize", "getschema"})
_EMBED_VERBS = frozenset({"embed", "distinct", "outliers"})


def _posture(verb: str) -> str:
    if verb in _FREE_VERBS:
        return "free"
    if verb in _EMBED_VERBS:
        return "embeddings"
    return "model calls"


class _PrefixedStderr(io.TextIOBase):
    """Line-prefixes a stage's stderr so interleaved receipts stay readable."""

    def __init__(self, prefix: str, target: object) -> None:
        self.prefix = prefix
        self.target = target
        self._at_line_start = True

    def write(self, text: str) -> int:
        for line in text.splitlines(keepends=True):
            if self._at_line_start and line.strip():
                self.target.write(self.prefix)  # type: ignore[attr-defined]
            self.target.write(line)  # type: ignore[attr-defined]
            self._at_line_start = line.endswith("\n")
        return len(text)

    def flush(self) -> None:
        self.target.flush()  # type: ignore[attr-defined]

    def isatty(self) -> bool:
        return False  # stage receipts never animate spinners


@click.command(name="run", context_settings={"ignore_unknown_options": True})
@click.argument("script", type=click.Path(exists=True, dir_okay=False, path_type=Path))
@click.option("--dry-run", "dry_run", is_flag=True, help="Print the pipeline graph; run nothing.")
@click.argument("extra", nargs=-1, type=click.UNPROCESSED)
def run_command(script: Path, dry_run: bool, extra: tuple[str, ...]) -> None:
    """Execute a .sem file — one stage, or a whole pipeline.

    \b
    Examples:
      smartpipe run extract.sem < cards.txt
      smartpipe run triage.sem --dry-run        # the graph + cost posture, zero calls
      cat tickets.log | smartpipe run triage.sem > report.txt

    A single-stage file is TOML pinning one verb invocation. A pipeline file
    holds [stage.NAME] tables run in order — each stage reads the previous
    stage's output (or 'input = "name"' picks an earlier one); the first
    reads stdin, the last writes stdout. Extra flags apply to single-stage
    files only.
    """
    execute_script(script, extra=extra, dry_run=dry_run)


def execute_script(script: Path, *, extra: tuple[str, ...] = (), dry_run: bool = False) -> None:
    """Run a .sem file (single stage or pipeline) — shared by ``smartpipe run``
    and user-named custom verbs (D39/06)."""
    stages = parse_pipeline(script)
    if stages is None:
        single = parse_single(script)
        if dry_run:
            argv = single.argv
            click.echo(f"{script.name}: {' '.join(argv)}   [{_posture(argv[0])}]")
            return
        with _strict_rows_default(opt_out=single.strict_rows is False):
            _invoke(list(single.argv) + list(extra))
        return
    if extra:
        raise click.UsageError("extra flags apply to single-stage files only")
    if dry_run:
        for stage in stages:
            click.echo(
                f"stage {stage.name:<12} {' '.join(stage.argv)}   [{_posture(stage.argv[0])}]"
            )
        return
    _run_pipeline(stages)


@contextlib.contextmanager
def _strict_rows_default(*, opt_out: bool) -> Generator[None]:
    """Item 19: a .sem execution behaves as if --strict-rows were set — the
    existing SMARTPIPE_STRICT_ROWS machinery, defaulted ON for the scope of one
    stage. An explicit ``strict-rows = false`` opts the stage out; a user-set
    env var (or a --strict-rows flag in the argv) still wins, so the default
    only ever fills the gap."""
    if opt_out or os.environ.get("SMARTPIPE_STRICT_ROWS", "").strip():
        yield
        return
    os.environ["SMARTPIPE_STRICT_ROWS"] = "1"
    try:
        yield
    finally:
        os.environ.pop("SMARTPIPE_STRICT_ROWS", None)


def _invoke(argv: list[str]) -> None:
    context = click.get_current_context()
    root = context.find_root().command
    assert isinstance(root, click.Group)  # run is only ever registered on the group
    verb = root.get_command(context, argv[0])
    assert verb is not None  # the translator only emits real verbs
    sub = verb.make_context(argv[0], list(argv[1:]), parent=context, ignore_unknown_options=False)
    with sub:
        verb.invoke(sub)


def _run_pipeline(stages: tuple[Stage, ...]) -> None:
    outputs: dict[str, str] = {}
    previous: str | None = None
    real_stdin, real_stdout, real_stderr = sys.stdin, sys.stdout, sys.stderr
    for position, stage in enumerate(stages):
        last = position == len(stages) - 1
        source = stage.input_name if stage.input_name is not None else previous
        stage_in = real_stdin if source is None else io.StringIO(outputs[source])
        stage_out = real_stdout if last else io.StringIO()
        sys.stdin = stage_in  # type: ignore[assignment]
        sys.stdout = stage_out  # type: ignore[assignment]
        sys.stderr = _PrefixedStderr(f"[{stage.name}] ", real_stderr)  # type: ignore[assignment]
        progress.set_stage_label(stage.name)  # status lines wear the receipt prefix
        try:
            with _strict_rows_default(opt_out=stage.strict_rows is False):
                _invoke(list(stage.argv))
        finally:
            progress.set_stage_label(None)
            sys.stdin, sys.stdout, sys.stderr = real_stdin, real_stdout, real_stderr
        if not last:
            assert isinstance(stage_out, io.StringIO)
            outputs[stage.name] = stage_out.getvalue()
        previous = stage.name
