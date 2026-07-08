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
    "MenuKey",
    "arrow_choose",
    "decode_key",
    "menu_capable",
    "numbered_choose",
    "render_menu",
    "step",
]


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


def step(index: int, key: MenuKey, count: int) -> int:
    """Cursor movement with wraparound; anything but UP/DOWN stays put."""
    match key:
        case MenuKey.UP:
            return (index - 1) % count
        case MenuKey.DOWN:
            return (index + 1) % count
        case _:
            return index


def menu_capable(*, stdin_tty: bool, stdout_tty: bool, term: str | None) -> bool:
    """Raw-mode arrows need a real terminal on BOTH streams and a TERM that
    understands ANSI. Anything else takes the numbered prompt."""
    if not (stdin_tty and stdout_tty):
        return False
    return term != "dumb"  # None = Windows consoles (VT enabled at startup)


_MARKER = "❯"  # noqa: RUF001 — the pinned cursor mark, not a mistyped '>'


def render_menu(labels: Sequence[str], index: int) -> str:
    """One frame: every row cleared and redrawn, the cursor row marked (cyan)."""
    rows = (
        f"\x1b[2K\x1b[36m  {_MARKER} {label}\x1b[0m" if i == index else f"\x1b[2K    {label}"
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
    a number, an exact label, or a label's first word; two strikes, then None."""
    say(title)
    for position, label in enumerate(labels, 1):
        say(f"  {position}. {label}")
    question = f"Pick [1-{len(labels)}]"
    answer = ask(question, str(start + 1))
    for attempt in range(2):
        picked = _match(answer, labels)
        if picked is not None:
            return picked
        if attempt == 0:
            answer = ask(f"{question} (a number, or the exact name)", str(start + 1))
    return None


def _match(answer: str, labels: Sequence[str]) -> int | None:
    cleaned = answer.strip()
    if cleaned.isdigit() and 1 <= int(cleaned) <= len(labels):
        return int(cleaned) - 1
    for position, label in enumerate(labels):
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
    index = start
    stream.write(f"{title}\n")
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
            index = step(index, key, len(labels))
            stream.write(f"\x1b[{len(labels)}A")
            stream.write(render_menu(labels, index))
            stream.flush()
    finally:
        stream.write("\x1b[?25h")
        stream.flush()


def _read_key() -> str:  # pragma: no cover — raw terminal I/O
    if sys.platform == "win32":
        import msvcrt

        first = msvcrt.getwch()
        if first == "\x03":
            raise KeyboardInterrupt
        if first in ("\x00", "\xe0"):  # arrow keys arrive as a two-char scan code
            return {"H": "\x1b[A", "P": "\x1b[B"}.get(msvcrt.getwch(), "")
        return first
    import select
    import termios
    import tty

    fd = sys.stdin.fileno()
    saved = termios.tcgetattr(fd)
    try:
        tty.setcbreak(fd)
        first = sys.stdin.read(1)
        if first == "\x03":
            raise KeyboardInterrupt
        if first != "\x1b":
            return first
        sequence = first
        while len(sequence) < 3 and select.select([sys.stdin], [], [], 0.05)[0]:
            sequence += sys.stdin.read(1)
        return sequence
    finally:
        termios.tcsetattr(fd, termios.TCSADRAIN, saved)
