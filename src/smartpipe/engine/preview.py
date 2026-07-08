"""Media-preview arithmetic — pure.

Everything about terminal media previews that is decidable without I/O lives
here: whether to render at all, where to sample a video strip, how to bucket a
waveform into a peak envelope, how big a thumbnail may be (terminal cells are
about twice as tall as wide), how to assemble frames side by side, and the
exact play-link string (OSC 8). The io shell (``io/preview``) supplies bytes,
ffmpeg, and file checks; this module supplies the decisions — the whole truth
table is testable.
"""

from __future__ import annotations

from itertools import pairwise
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

__all__ = [
    "human_duration",
    "human_size",
    "join_columns",
    "peak_envelope",
    "play_line",
    "should_preview",
    "strip_seconds",
    "thumbnail_cells",
]

# The video strip samples: never 0% — intros are black/logo frames.
_STRIP_FRACTIONS = (0.1, 0.5, 0.9)


def should_preview(*, enabled: bool, color: bool) -> bool:
    """The render-or-not decision: previews need the ``media-previews`` config
    switch on AND ANSI color support. Piped output, NO_COLOR, and TERM=dumb all
    fail the color test, so their bytes stay identical to today's."""
    return enabled and color


def strip_seconds(duration: float) -> tuple[float, float, float]:
    """Where the 3-frame video strip samples: 10%/50%/90% of the duration."""
    first, middle, last = (duration * fraction for fraction in _STRIP_FRACTIONS)
    return (first, middle, last)


def thumbnail_cells(
    width: int, height: int, *, max_cols: int = 40, max_rows: int = 12
) -> tuple[int, int]:
    """Fit an image's pixel dimensions into a terminal-cell grid, preserving
    apparent aspect ratio (a cell renders ~1 pixel wide by ~2 pixels tall)."""
    if width <= 0 or height <= 0:
        return (1, 1)  # a lying header can't crash the preview
    scale = min(max_cols / width, (max_rows * 2) / height)
    return (max(1, round(width * scale)), max(1, round(height * scale / 2)))


def peak_envelope(samples: Sequence[int], buckets: int) -> tuple[float, ...]:
    """Bucket PCM samples into a peak envelope, normalized to the clip's own
    loudest sample (0.0 to 1.0) so quiet recordings still draw a visible shape."""
    if not samples:
        return ()
    count = min(buckets, len(samples))
    bounds = [round(index * len(samples) / count) for index in range(count + 1)]
    peaks = [max(abs(sample) for sample in samples[start:end]) for start, end in pairwise(bounds)]
    top = max(peaks)
    if top == 0:
        return tuple(0.0 for _ in peaks)
    return tuple(peak / top for peak in peaks)


def join_columns(
    columns: Sequence[tuple[Sequence[str], int]], *, gap: str = "  "
) -> tuple[str, ...]:
    """Assemble rendered blocks side by side (the video strip). Each column is
    (lines, visible cell width); short columns pad with spaces so later columns
    stay aligned."""
    if not columns:
        return ()
    height = max(len(lines) for lines, _ in columns)
    return tuple(
        gap.join(lines[row] if row < len(lines) else " " * cells for lines, cells in columns)
        for row in range(height)
    )


def human_duration(seconds: float) -> str:
    """A clock for the play link: ``0:42``, ``12:03``, ``1:02:03``."""
    whole = int(seconds)
    hours, rest = divmod(whole, 3600)
    minutes, secs = divmod(rest, 60)
    if hours:
        return f"{hours}:{minutes:02d}:{secs:02d}"
    return f"{minutes}:{secs:02d}"


def human_size(size: int) -> str:
    """Decoded byte counts in the media-summary voice: ``48 KB``, ``2.1 MB``."""
    if size >= 1_048_576:
        return f"{size / 1_048_576:.1f} MB"
    if size >= 1024:
        return f"{size // 1024} KB"
    return f"{size} B"


def play_line(
    *, url: str | None, path: str | None, seconds: float | None, size: int, color: bool
) -> str | None:
    """The playback "button": ``▶ play (0:42, 2.1 MB)`` wrapping a ``file://``
    URL as an OSC 8 hyperlink. None when the item has no on-disk source (media
    that exists only as pipe bytes gets no link — never a temp file just to
    have one). Without color support the escape codes are withheld and the
    plain path is printed instead."""
    if url is None or path is None:
        return None
    held = human_size(size) if seconds is None else f"{human_duration(seconds)}, {human_size(size)}"
    if not color:
        return f"▶ play {path} ({held})"
    return f"\x1b]8;;{url}\x1b\\▶ play ({held})\x1b]8;;\x1b\\"
