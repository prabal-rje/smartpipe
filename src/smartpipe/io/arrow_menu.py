"""A hand-rolled arrow-key menu — no new dependencies (the D46 fence).

The decisions are pure and tested: key decoding, cursor math, the rendered
frame, the capability gate, and the numbered fallback (which is the SAME menu
driven through the injected ask/say prompts, so pipes, tests, and TERM=dumb
keep working). Only ``arrow_choose`` touches raw terminal state, and it never
runs unless ``menu_capable`` said a real interactive terminal is present.
"""

from __future__ import annotations

import sys
from enum import Enum
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable, Sequence
    from typing import TextIO

__all__ = [
    "SEPARATOR",
    "MenuKey",
    "arrow_choose",
    "decode_key",
    "first_selectable",
    "index_of_ordinal",
    "menu_capable",
    "numbered_choose",
    "ordinal_of",
    "render_menu",
    "step",
]

# A blank label is a separator: a real row (it occupies a line in the frame)
# that can never hold the cursor, take a number, or be returned — the menus'
# paragraph breaks. Callers append it between groups, never leading/trailing.
SEPARATOR = ""


class MenuKey(Enum):
    UP = "up"
    DOWN = "down"
    ENTER = "enter"
    CANCEL = "cancel"
    OTHER = "other"


_SEQUENCES: dict[str, MenuKey] = {
    "\x1b[A": MenuKey.UP,
    "\x1bOA": MenuKey.UP,
    "k": MenuKey.UP,
    "\x1b[B": MenuKey.DOWN,
    "\x1bOB": MenuKey.DOWN,
    "j": MenuKey.DOWN,
    "\r": MenuKey.ENTER,
    "\n": MenuKey.ENTER,
    "\x1b": MenuKey.CANCEL,  # a bare Escape
    "q": MenuKey.CANCEL,
}


def decode_key(sequence: str) -> MenuKey:
    return _SEQUENCES.get(sequence, MenuKey.OTHER)


def step(index: int, key: MenuKey, labels: Sequence[str]) -> int:
    """Cursor movement with wraparound, sliding past separator rows; anything
    but UP/DOWN stays put. An all-separator list (never built) stays put too."""
    match key:
        case MenuKey.UP:
            delta = -1
        case MenuKey.DOWN:
            delta = 1
        case _:
            return index
    count = len(labels)
    position = index
    for _ in range(count):
        position = (position + delta) % count
        if labels[position] != SEPARATOR:
            return position
    return index


def first_selectable(labels: Sequence[str], start: int) -> int:
    """The first non-separator row at or after ``start`` (wrapping) — both
    drivers normalize their start through this so the cursor can never open
    on a blank row."""
    count = len(labels)
    for offset in range(count):
        position = (start + offset) % count
        if labels[position] != SEPARATOR:
            return position
    return start


def ordinal_of(labels: Sequence[str], index: int) -> int:
    """``index``'s 1-based position among the SELECTABLE rows — the number the
    fallback prints next to it (separators are unnumbered)."""
    return sum(1 for label in labels[: index + 1] if label != SEPARATOR)


def index_of_ordinal(labels: Sequence[str], ordinal: int) -> int | None:
    """The raw row index of the ``ordinal``-th selectable label, or None when
    the number is out of range — a typed digit can never land on a separator."""
    seen = 0
    for position, label in enumerate(labels):
        if label == SEPARATOR:
            continue
        seen += 1
        if seen == ordinal:
            return position
    return None


def menu_capable(*, stdin_tty: bool, stdout_tty: bool, term: str | None) -> bool:
    """Raw-mode arrows need a real terminal on BOTH streams and a TERM that
    understands ANSI. Anything else takes the numbered prompt."""
    if not (stdin_tty and stdout_tty):
        return False
    return term != "dumb"  # None = Windows consoles (VT enabled at startup)


_MARKER = "❯"  # noqa: RUF001 — the pinned cursor mark, not a mistyped '>'


def render_menu(labels: Sequence[str], index: int) -> str:
    """One frame: every row cleared and redrawn, the cursor row marked (cyan);
    separator rows are cleared blank lines — the menu's paragraph breaks."""
    rows = (
        "\x1b[2K"
        if label == SEPARATOR
        else (f"\x1b[2K\x1b[36m  {_MARKER} {label}\x1b[0m" if i == index else f"\x1b[2K    {label}")
        for i, label in enumerate(labels)
    )
    return "\n".join(rows) + "\n"


def numbered_choose(
    title: str,
    labels: Sequence[str],
    start: int,
    *,
    ask: Callable[[str, str], str],
    say: Callable[[str], None],
) -> int | None:
    """The typed fallback: a numbered list on the wizard's own prompts. Accepts
    a number, an exact label, or a label's first word; two strikes, then None.
    Separators print as blank lines and take no number — the numbering stays
    contiguous over the selectable rows, so typed digits survive regrouping."""
    say(title)
    say("")
    shown = 0
    for label in labels:
        if label == SEPARATOR:
            say("")
            continue
        shown += 1
        say(f"  {shown}. {label}")
    question = f"Pick [1-{shown}]"
    default = str(ordinal_of(labels, first_selectable(labels, start)))
    answer = ask(question, default)
    for attempt in range(2):
        picked = _match(answer, labels)
        if picked is not None:
            return picked
        if attempt == 0:
            answer = ask(f"{question} (a number, or the exact name)", default)
    return None


def _match(answer: str, labels: Sequence[str]) -> int | None:
    """A typed answer's RAW row index (the choose contract) — digits map
    through the selectable ordinals, labels skip separators, so neither path
    can ever resolve to a blank row."""
    cleaned = answer.strip()
    if cleaned.isdigit():
        return index_of_ordinal(labels, int(cleaned))
    for position, label in enumerate(labels):
        if label == SEPARATOR:
            continue
        first_word = label.split()[0] if label.split() else label
        if cleaned in (label, first_word):
            return position
    return None


def arrow_choose(
    title: str,
    labels: Sequence[str],
    stream: TextIO,
    *,
    start: int = 0,
) -> int | None:  # pragma: no cover — raw terminal I/O; decisions are tested above
    """Drive the menu with real arrow keys. Returns the index, or None on q/Esc."""
    index = first_selectable(labels, start)
    stream.write(f"{title}\n\n")
    stream.write("\x1b[?25l")  # hide the cursor while the menu owns the rows
    try:
        stream.write(render_menu(labels, index))
        stream.flush()
        while True:
            key = decode_key(_read_key())
            if key is MenuKey.ENTER:
                return index
            if key is MenuKey.CANCEL:
                return None
            index = step(index, key, labels)
            stream.write(f"\x1b[{len(labels)}A")
            stream.write(render_menu(labels, index))
            stream.flush()
    finally:
        stream.write("\x1b[?25h")
        stream.flush()


def read_sequence(read1: Callable[[], str], pending: Callable[[], bool]) -> str:
    """One keypress from injected byte-level primitives — the escape-sequence
    accumulation, separated from raw-terminal I/O so it is testable.

    An arrow arrives as 3 bytes (CSI ``\\x1b[A`` or application-mode SS3
    ``\\x1bOA``) that land TOGETHER: the reader must drain what is pending
    at the byte layer, never through a buffered stream (the owner-hit bug:
    ``sys.stdin.read(1)`` slurped the whole sequence into Python's buffer,
    ``select`` on the fd then saw nothing, and a bare ESC cancelled the menu).
    """
    first = read1()
    if first == "\x03":
        raise KeyboardInterrupt
    if first != "\x1b":
        return first
    sequence = first
    while len(sequence) < 3 and pending():
        sequence += read1()
    return sequence


def _read_key() -> str:  # pragma: no cover — raw terminal I/O
    if sys.platform == "win32":
        import msvcrt

        first = msvcrt.getwch()
        if first == "\x03":
            raise KeyboardInterrupt
        if first in ("\x00", "\xe0"):  # arrow keys arrive as a two-char scan code
            return {"H": "\x1b[A", "P": "\x1b[B"}.get(msvcrt.getwch(), "")
        return first
    import os
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        # os.read on the fd, never sys.stdin: buffering must not eat the tail
        return read_sequence(
            lambda: os.read(fd, 1).decode("utf-8", "replace"),
            lambda: bool(select.select([fd], [], [], 0.05)[0]),
        )
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
