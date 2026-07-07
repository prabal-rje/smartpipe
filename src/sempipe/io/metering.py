"""Run telemetry (D40): observed units, never estimated dollars.

A module-level, run-scoped meter — the documented diagnostics-style exception
to no-globals (one verb per process; ``reset()`` at container build and in
tests). Numbers come from provider usage fields and real byte counts; when a
wire omits usage, the meter under-counts rather than lies.
"""

from __future__ import annotations

import io
import wave
from dataclasses import dataclass, field
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Sequence

    from sempipe.models.base import MediaData

__all__ = [
    "add_conversion",
    "add_request_media",
    "add_tokens",
    "receipt",
    "reset",
    "snapshot",
    "status_segment",
]


@dataclass(slots=True)
class _Meter:
    tokens_in: int = 0
    tokens_out: int = 0
    media_bytes: dict[str, int] = field(default_factory=dict[str, int])
    media_count: dict[str, int] = field(default_factory=dict[str, int])
    audio_seconds: float = 0.0
    conversions: int = 0


_state = _Meter()


def reset() -> None:
    global _state
    _state = _Meter()


def add_tokens(*, tokens_in: int = 0, tokens_out: int = 0) -> None:
    _state.tokens_in += max(0, tokens_in)
    _state.tokens_out += max(0, tokens_out)


def add_conversion() -> None:
    """One PAID conversion (cloud caption/hear/watch/STT) — local whisper is
    free and uncounted."""
    _state.conversions += 1


def add_request_media(parts: Sequence[MediaData]) -> None:
    """Meter media actually being SENT (call after pre-send refusals — a
    refused part costs nothing)."""
    from sempipe.models.base import AudioData, ImageData, VideoData

    for part in parts:
        match part:
            case ImageData():
                kind = "image"
            case AudioData():
                kind = "audio"
                _state.audio_seconds += _wav_seconds(part.data, part.mime) or 0.0
            case VideoData():
                kind = "video"
            case _ as unreachable:  # pragma: no cover — the union is closed
                from typing import assert_never

                assert_never(unreachable)
        _state.media_bytes[kind] = _state.media_bytes.get(kind, 0) + len(part.data)
        _state.media_count[kind] = _state.media_count.get(kind, 0) + 1


def _wav_seconds(data: bytes, mime: str) -> float | None:
    if mime not in ("audio/wav", "audio/x-wav"):
        return None  # other containers need ffprobe; bytes-only is honest enough
    try:
        with wave.open(io.BytesIO(data)) as clip:
            rate = clip.getframerate()
            return clip.getnframes() / rate if rate else None
    except (wave.Error, EOFError, OSError, ValueError, RuntimeError):
        return None  # malformed RIFF — bytes-only is honest enough


@dataclass(frozen=True, slots=True)
class Snapshot:
    tokens_in: int
    tokens_out: int
    media_bytes: dict[str, int]
    media_count: dict[str, int]
    audio_seconds: float
    conversions: int

    @property
    def empty(self) -> bool:
        return not (self.tokens_in or self.tokens_out or self.media_bytes or self.conversions)


def snapshot() -> Snapshot:
    return Snapshot(
        tokens_in=_state.tokens_in,
        tokens_out=_state.tokens_out,
        media_bytes=dict(_state.media_bytes),
        media_count=dict(_state.media_count),
        audio_seconds=_state.audio_seconds,
        conversions=_state.conversions,
    )


# --- formatting --------------------------------------------------------------------


def _count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def _megabytes(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB"


def _duration(seconds: float) -> str:
    whole = int(seconds)
    if whole >= 60:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole}s"


def status_segment() -> str:
    """The live status-line segment; empty string when nothing was consumed."""
    view = snapshot()
    if view.empty:
        return ""
    pieces = [f"↑{_count(view.tokens_in)} ↓{_count(view.tokens_out)} tok"]
    for kind, label in (("image", "img"), ("video", "vid")):
        size = view.media_bytes.get(kind)
        if size:
            pieces.append(f"{_megabytes(size)} {label}")
    if view.media_bytes.get("audio"):
        held = (
            _duration(view.audio_seconds)
            if view.audio_seconds
            else _megabytes(view.media_bytes["audio"])
        )
        pieces.append(f"{held} audio")
    return " · ".join(pieces)


def receipt() -> str | None:
    """The end-of-run totals line — the number that goes in the report."""
    view = snapshot()
    if view.empty:
        return None
    pieces = [f"{_count(view.tokens_in)} in · {_count(view.tokens_out)} out tokens"]
    for kind, plural in (("image", "images"), ("video", "video")):
        size = view.media_bytes.get(kind)
        if size:
            pieces.append(f"{_megabytes(size)} {plural} ({view.media_count[kind]})")
    audio_size = view.media_bytes.get("audio")
    if audio_size:
        timed = f" · {_duration(view.audio_seconds)}" if view.audio_seconds else ""
        pieces.append(f"{_megabytes(audio_size)} audio ({view.media_count['audio']}){timed}")
    if view.conversions:
        pieces.append(f"{view.conversions} paid conversions")
    return "run: " + " · ".join(pieces)
