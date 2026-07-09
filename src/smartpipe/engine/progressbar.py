"""The determinate progress bar (ledger item 67) — pure string rendering.

``render_bar`` turns run counters into the one status line a known-total run
shows: fill, percent, counts, rate, and the time left. All math is pure — the
rate arrives as a parameter (the clock lives in ``io/progress``), so every
state is a table test. Unicode blocks by default; the ASCII fallback
(``[=====>....]``) follows the spinner's own plain-terminal rule.
"""

from __future__ import annotations

import math

__all__ = ["format_eta", "render_bar"]

_DEFAULT_WIDTH = 15
_MIN_WIDTH = 5  # narrower than this stops reading as a bar
_RATE_INTEGER_FROM = 10.0  # fast runs drop the decimal: 12/s, not 12.0/s


def format_eta(seconds: float) -> str:
    """Whole units, largest two: ``45s`` · ``2m12s`` · ``1h2m``."""
    total = int(seconds)
    hours, remainder = divmod(total, 3600)
    minutes, secs = divmod(remainder, 60)
    if hours:
        return f"{hours}h{minutes}m"
    if minutes:
        return f"{minutes}m{secs}s"
    return f"{secs}s"


def render_bar(
    done: int,
    total: int,
    *,
    rate: float,
    width: int = _DEFAULT_WIDTH,
    ascii_only: bool = False,
    label: str | None = None,
) -> str:
    """The known-total status line: ``[██████░░░] 41% · 205/500 · 12/s · ~25s left``.

    The fill and percent truncate, so 100% (and a full bar) is earned only at
    ``done == total``. The ETA appears once anything is done and the rate is a
    usable positive finite number; it disappears again at completion. A stage
    ``label`` prefixes the line the way pipeline receipts are prefixed.
    """
    width = max(width, _MIN_WIDTH)
    fraction = min(done / total, 1.0) if total else 1.0
    filled = int(fraction * width)
    segments = [f"[{_cells(filled, width, ascii_only)}] {int(fraction * 100)}% · {done}/{total}"]
    if math.isfinite(rate) and rate > 0:
        segments.append(f"{rate:.0f}/s" if rate >= _RATE_INTEGER_FROM else f"{rate:.1f}/s")
        if 0 < done < total:
            segments.append(f"~{format_eta(round((total - done) / rate))} left")
    line = " · ".join(segments)
    return f"[{label}] {line}" if label is not None else line


def _cells(filled: int, width: int, ascii_only: bool) -> str:
    if not ascii_only:
        return "█" * filled + "░" * (width - filled)
    if filled == 0:
        return "." * width
    if filled == width:
        return "=" * width
    return "=" * (filled - 1) + ">" + "." * (width - filled)
