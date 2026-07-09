"""The schema workshop shell — bare ``smartpipe schema`` at a TTY (all free).

The loop is terminal-free: every effect arrives as an injected callable
(picker-style DI), so tests script whole sessions without a terminal.
``PinnedScreen`` is the rich-mode drawer — alt screen, the header pinned in
the top rows, a scroll region for the transcript below (the leaderboard's
repaint-in-place idea). Anything less capable (piped stdout, TERM=dumb, a
tiny window) gets the plain loop with the header reprinted after each command.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, assert_never

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.engine.schema import example_instance
from smartpipe.engine.schema_repl import (
    AddCommand,
    CheckCommand,
    Command,
    DraftField,
    DropCommand,
    ExampleCommand,
    QuitCommand,
    ReplaceCommand,
    SaveCommand,
    UnknownInput,
    add_field,
    aggregate_coverage,
    compile_draft,
    coverage_lines,
    draft_from_braces,
    drop_field,
    header_lines,
    paint,
    parse_command,
    paste_lines,
)

if TYPE_CHECKING:
    from collections.abc import Callable
    from typing import TextIO

__all__ = ["PinnedScreen", "WorkshopResult", "run_workshop", "workshop_entry"]

_PROMPT = "schema> "
_HEADER_ROWS = 5  # the four header lines plus one separator row


@dataclass(frozen=True, slots=True)
class WorkshopResult:
    """What the session ended with — the caller prints the paste-ready lines."""

    draft: tuple[DraftField, ...]
    saved: str | None
    discarded: bool


def run_workshop(
    *,
    ask: Callable[[str], str],
    say: Callable[[str], None],
    draw: Callable[[tuple[str, ...]], None],
    confirm: Callable[[str], bool],
    color: bool = False,
) -> WorkshopResult:
    """The command loop: draw the header, read one line, apply it. ``ask`` may
    raise EOFError (= /quit) or KeyboardInterrupt (one discard question, once)."""
    draft: tuple[DraftField, ...] = ()
    saved: str | None = None
    asked_discard = False
    while True:
        draw(header_lines(draft, color=color))
        try:
            line = ask(_PROMPT)
        except EOFError:
            break
        except KeyboardInterrupt:
            if not draft or asked_discard or confirm("discard draft? [y/N]"):
                return WorkshopResult(draft, saved, discarded=True)
            asked_discard = True
            continue
        outcome = _apply(parse_command(line), draft, saved, say=say, color=color)
        if outcome is None:
            break
        draft, saved = outcome
    return WorkshopResult(draft, saved, discarded=False)


def _apply(
    command: Command | None,
    draft: tuple[DraftField, ...],
    saved: str | None,
    *,
    say: Callable[[str], None],
    color: bool,
) -> tuple[tuple[DraftField, ...], str | None] | None:
    """One command against the draft: the new (draft, saved) — None means quit."""
    match command:
        case None:
            return draft, saved
        case AddCommand(name=name, type_text=type_text, guidance=guidance):
            added = _add(draft, DraftField(name, type_text, guidance), say=say, color=color)
            return added, saved
        case DropCommand(name=name):
            dropped = drop_field(draft, name)
            if dropped is None:
                _report(say, f"no field '{name}' in the draft", color)
                return draft, saved
            say(_ok(f"dropped {name}", color))
            return dropped, saved
        case CheckCommand(path=path):
            _check(draft, path, say=say, color=color)
            return draft, saved
        case ExampleCommand():
            schema = _compiled(draft, say=say, color=color)
            if schema is not None:
                say(json.dumps(example_instance(schema), indent=2, ensure_ascii=False))
            return draft, saved
        case SaveCommand(path=path):
            return draft, _save(draft, path, saved, say=say, color=color)
        case QuitCommand():
            return None
        case ReplaceCommand(text=text):
            return _replace(draft, text, say=say, color=color), saved
        case UnknownInput(message=message):
            _report(say, message, color)
            return draft, saved
        case _ as unreachable:  # pragma: no cover — pyright proves exhaustiveness
            assert_never(unreachable)


def _ok(text: str, color: bool) -> str:
    return paint(f"✓ {text}", "32", color)


def _report(say: Callable[[str], None], message: str, color: bool) -> None:
    """Errors land in the transcript in red — including the multi-line
    compiler screens, painted line by line."""
    for line in message.splitlines():
        say(paint(line, "31", color))


def _compiled(
    draft: tuple[DraftField, ...], *, say: Callable[[str], None], color: bool
) -> dict[str, object] | None:
    try:
        return compile_draft(draft)
    except UsageFault as fault:
        _report(say, str(fault), color)
        return None


def _add(
    draft: tuple[DraftField, ...],
    field: DraftField,
    *,
    say: Callable[[str], None],
    color: bool,
) -> tuple[DraftField, ...]:
    candidate = add_field(draft, field)
    try:
        compile_draft(candidate)  # the real compiler is the validator
    except UsageFault as fault:
        _report(say, str(fault), color)
        return draft
    verb = "replaced" if any(existing.name == field.name for existing in draft) else "added"
    say(_ok(f"{verb} {field.name}", color))
    return candidate


def _replace(
    draft: tuple[DraftField, ...], text: str, *, say: Callable[[str], None], color: bool
) -> tuple[DraftField, ...]:
    try:
        fresh = draft_from_braces(text)
    except UsageFault as fault:
        _report(say, str(fault), color)
        return draft
    count = len(fresh)
    say(_ok(f"draft replaced · {count} field" + ("" if count == 1 else "s"), color))
    return fresh


def _check(
    draft: tuple[DraftField, ...], path: str, *, say: Callable[[str], None], color: bool
) -> None:
    schema = _compiled(draft, say=say, color=color)
    if schema is None:
        return
    try:
        text = Path(path).read_text(encoding="utf-8")
    except OSError as exc:
        _report(say, f"can't read {path} ({exc.strerror or exc})", color)
        return
    for line in coverage_lines(aggregate_coverage(schema, text.splitlines()), color=color):
        say(line)


def _save(
    draft: tuple[DraftField, ...],
    path: str,
    saved: str | None,
    *,
    say: Callable[[str], None],
    color: bool,
) -> str | None:
    schema = _compiled(draft, say=say, color=color)
    if schema is None:
        return saved
    try:
        Path(path).write_text(
            json.dumps(schema, indent=2, ensure_ascii=False) + "\n", encoding="utf-8"
        )
    except OSError as exc:
        _report(say, f"can't write {path} ({exc.strerror or exc})", color)
        return saved
    say(_ok(f"wrote {path}", color))
    for line in paste_lines(draft, saved=path):
        say(line)
    return path


# --- rich mode: the pinned header over a scroll region ---------------------------------


@dataclass(slots=True)
class PinnedScreen:
    """Alt screen with the header pinned in rows 1..N and a DECSTBM scroll
    region below it: the transcript scrolls, the header repaints in place.
    Leaving the context restores the caller's screen untouched."""

    stream: TextIO
    rows: int
    header_rows: int = _HEADER_ROWS

    def __enter__(self) -> PinnedScreen:
        self.stream.write("\x1b[?1049h\x1b[2J\x1b[H")  # alt screen, cleared
        self.stream.write(f"\x1b[{self.header_rows + 1};{self.rows}r")  # transcript region
        self.stream.write(f"\x1b[{self.rows};1H")  # the prompt lives at the bottom
        self.stream.flush()
        return self

    def draw(self, lines: tuple[str, ...]) -> None:
        self.stream.write("\x1b7\x1b[?7l\x1b[H")  # save cursor; no wrap while up top
        for line in lines:
            self.stream.write(f"{line}\x1b[K\n")
        self.stream.write("\x1b[K\x1b[?7h\x1b8")  # blank separator row, wrap on, return
        self.stream.flush()

    def __exit__(self, *exc_info: object) -> None:
        self.stream.write("\x1b[r\x1b[?1049l")  # region reset, alt screen off
        self.stream.flush()


def _plain_draw(lines: tuple[str, ...], say: Callable[[str], None]) -> None:
    for line in lines:
        say(line)


def workshop_entry() -> ExitCode:
    """Wire the loop to the real terminal. Rich mode needs the same terminal
    capabilities as the arrow menu, plus enough rows for the pinned header;
    the paste-ready lines land AFTER teardown so they outlive the alt screen."""
    import contextlib
    import os
    import shutil
    import sys
    from functools import partial

    import click

    from smartpipe.io import tty
    from smartpipe.io.arrow_menu import menu_capable

    with contextlib.suppress(ImportError):  # free line editing where the platform has it
        import readline  # noqa: F401  # pyright: ignore[reportUnusedImport]

    color = tty.stdout_supports_color()
    rows = shutil.get_terminal_size((80, 24)).lines
    rich = (
        menu_capable(
            stdin_tty=sys.stdin.isatty(),
            stdout_tty=sys.stdout.isatty(),
            term=os.environ.get("TERM"),
        )
        and rows >= _HEADER_ROWS + 3
    )

    def ask(prompt: str) -> str:
        return input(prompt)

    def confirm(question: str) -> bool:
        try:
            return input(f"{question} ").strip().lower() in ("y", "yes")
        except (EOFError, KeyboardInterrupt):
            return True  # a second interrupt means leave

    if rich:  # pragma: no cover — real-terminal wiring; the loop and drawer are tested
        with PinnedScreen(stream=sys.stdout, rows=rows) as screen:
            result = run_workshop(
                ask=ask, say=click.echo, draw=screen.draw, confirm=confirm, color=color
            )
    else:
        result = run_workshop(
            ask=ask,
            say=click.echo,
            draw=partial(_plain_draw, say=click.echo),
            confirm=confirm,
            color=color,
        )
    if not result.discarded:
        for line in paste_lines(result.draft, saved=result.saved):
            click.echo(line)
    return ExitCode.OK
