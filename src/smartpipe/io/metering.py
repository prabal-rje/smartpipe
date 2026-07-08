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

    from smartpipe.models.base import MediaData

__all__ = [
    "add_conversion",
    "add_request_media",
    "add_tokens",
    "clip_seconds",
    "count",
    "duration",
    "megabytes",
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
    from smartpipe.models.base import AudioData, ImageData, VideoData

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


def clip_seconds(data: bytes, mime: str) -> float | None:
    """Duration of one audio/video clip — the probe behind media-aware token
    estimation (D26 v2). WAV headers parse pure; every other container asks
    ffmpeg when it's around; ``None`` means unknowable (callers fall back to a
    conservative per-MB rate)."""
    seconds = _wav_seconds(data, mime)
    if seconds is not None:
        return seconds
    exe = _ffmpeg_exe()
    if exe is None:
        return None
    return _banner_seconds(exe, data)


def _ffmpeg_exe() -> str | None:
    from smartpipe.core.errors import ItemError
    from smartpipe.parsing.extract import ffmpeg_exe

    try:
        return ffmpeg_exe()
    except ItemError:
        return None  # no ffmpeg anywhere — the per-MB fallback stands in


def _banner_seconds(exe: str, data: bytes) -> float | None:
    import os
    import tempfile

    from smartpipe.core.errors import ItemError
    from smartpipe.parsing.extract import ffprobe_duration

    handle, path = tempfile.mkstemp(prefix="smartpipe-clip-")
    try:
        with os.fdopen(handle, "wb") as sink:
            sink.write(data)
        return ffprobe_duration(exe, path)
    except (ItemError, OSError):
        return None  # ffmpeg couldn't read it — honest unknowable
    finally:
        os.unlink(path)


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


def count(value: int) -> str:
    if value >= 1_000_000:
        return f"{value / 1_000_000:.1f}M"
    if value >= 1_000:
        return f"{value / 1_000:.1f}k"
    return str(value)


def megabytes(size: int) -> str:
    return f"{size / 1_048_576:.1f} MB"


def duration(seconds: float) -> str:
    whole = int(seconds)
    if whole >= 60:
        return f"{whole // 60}m{whole % 60:02d}s"
    return f"{whole}s"


def status_segment() -> str:
    """The live status-line segment; empty string when nothing was consumed."""
    view = snapshot()
    if view.empty:
        return ""
    pieces = [f"↑{count(view.tokens_in)} ↓{count(view.tokens_out)} tok"]
    for kind, label in (("image", "img"), ("video", "vid")):
        size = view.media_bytes.get(kind)
        if size:
            pieces.append(f"{megabytes(size)} {label}")
    if view.media_bytes.get("audio"):
        held = (
            duration(view.audio_seconds)
            if view.audio_seconds
            else megabytes(view.media_bytes["audio"])
        )
        pieces.append(f"{held} audio")
    return " · ".join(pieces)


def receipt() -> str | None:
    """The end-of-run totals line — the number that goes in the report."""
    view = snapshot()
    if view.empty:
        return None
    # arrows match the live status line (↑ sent to the model, ↓ received) —
    # one symbol language across spinner and receipt (owner unification)
    pieces = [f"↑{count(view.tokens_in)} ↓{count(view.tokens_out)} tok"]
    for kind, plural in (("image", "images"), ("video", "video")):
        size = view.media_bytes.get(kind)
        if size:
            pieces.append(f"{megabytes(size)} {plural} ({view.media_count[kind]})")
    audio_size = view.media_bytes.get("audio")
    if audio_size:
        timed = f" · {duration(view.audio_seconds)}" if view.audio_seconds else ""
        pieces.append(f"{megabytes(audio_size)} audio ({view.media_count['audio']}){timed}")
    if view.conversions:
        pieces.append(f"{view.conversions} paid conversions")
    return "run: " + " · ".join(pieces)
