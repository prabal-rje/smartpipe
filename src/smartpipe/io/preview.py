"""Terminal media previews — the imperative shell.

Under the ``__media`` summary line of the YAML-ish block (the TTY preview and
the ``readable`` verb — one renderer, two homes) this module draws the media
itself: a plotext color thumbnail for images, a 3-frame strip sampled at
10%/50%/90% for video (never 0% — intros are black/logo frames), a peak-
envelope waveform for audio, and an OSC 8 ``▶ play`` hyperlink when the item's
``__source`` spine still names a real file on disk. Media that exists only as
pipe bytes still gets its picture — the play link is simply omitted (never a
temp file just to have something to link).

All decisions are pure (``engine/preview``); this module owns ffmpeg calls
(the metering temp-file pattern), file checks, and plotext rendering. Callers
gate through ``maybe_preview`` — when previews are off, output is piped, or
color is unsupported (NO_COLOR, TERM=dumb) the hook is ``None`` and every
byte stays identical to today's. plotext (and PIL beneath it) imports stay
function-local: the startup budget never pays for previews. A part that can't
be decoded degrades to one dim ``(no preview: …)`` line — the summary line
above it still tells the truth.
"""

from __future__ import annotations

import os
from typing import TYPE_CHECKING, assert_never

from smartpipe.core.errors import ItemError
from smartpipe.engine.preview import (
    join_columns,
    peak_envelope,
    play_line,
    should_preview,
    strip_seconds,
    thumbnail_cells,
)
from smartpipe.models.base import AudioData, ImageData, VideoData

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence
    from pathlib import Path

    from smartpipe.io.render import MediaLines
    from smartpipe.models.base import MediaData

__all__ = ["maybe_preview", "preview_lines"]

_INDENT = "  "  # preview lines sit nested under the __media summary line
_DIM = "\x1b[2m"
_RESET = "\x1b[0m"
_MAX_COLS = 40  # the thumbnail cap (cols x rows), per the ratified design
_MAX_ROWS = 12
_FRAME_COLS = 12  # per strip frame: 3 frames + gaps ≈ the image cap
_FRAME_ROWS = 6
_WAVE_ROWS = 6
_WAVE_RATE = 8_000  # mono low-rate PCM — plenty for a peak envelope
_DECODE_CAP_SECONDS = 600  # a 3-hour file can't stall the preview


def maybe_preview(*, enabled: bool, color: bool, width: int) -> MediaLines | None:
    """The injectable ``render_block`` hook, or None when previews must not
    render (kill switch off, piped output, NO_COLOR/TERM=dumb) — the None path
    keeps today's bytes untouched."""
    if not should_preview(enabled=enabled, color=color):
        return None
    from functools import partial

    return partial(preview_lines, color=color, width=width)


def preview_lines(record: Mapping[str, object], *, color: bool, width: int) -> list[str]:
    """Preview lines for the FIRST ``__media`` part of one record; remaining
    parts keep their summary-line treatment. Decode failures degrade to one
    dim ``(no preview: …)`` line instead of killing the run's output."""
    from smartpipe.io.items import media_parts

    parts = media_parts(record)
    if not parts:
        return []
    try:
        body = _rendered(parts[0], record, color=color, width=width)
    except (ItemError, OSError, ValueError) as exc:
        reason = next(iter(str(exc).splitlines()), "") or "unreadable media"
        note = f"(no preview: {reason})"
        body = [f"{_DIM}{note}{_RESET}" if color else note]
    return [f"{_INDENT}{line}" for line in body]


def _rendered(
    part: MediaData, record: Mapping[str, object], *, color: bool, width: int
) -> list[str]:
    cap = max(8, min(_MAX_COLS, width - len(_INDENT)))
    match part:
        case ImageData():
            lines, _cells = _thumbnail(part.data, max_cols=cap, max_rows=_MAX_ROWS)
            return lines
        case AudioData():
            waveform = _waveform(part, buckets=cap)
            return [*waveform, *_play(record, part, _clip_seconds(part), color=color)]
        case VideoData():
            strip, seconds = _film_strip(part)
            return [*strip, *_play(record, part, seconds, color=color)]
        case _ as unreachable:  # pragma: no cover — the union is closed
            assert_never(unreachable)


# --- images -------------------------------------------------------------------------


def _thumbnail(data: bytes, *, max_cols: int, max_rows: int) -> tuple[list[str], int]:
    """(rendered lines, cell width) — aspect ratio comes from the header-parsed
    dimensions (engine/chunking), the pixels from plotext."""
    from smartpipe.engine.chunking import image_dimensions

    dims = image_dimensions(data)
    if dims is None:
        raise ItemError("unrecognized image format")
    cols, rows = thumbnail_cells(*dims, max_cols=max_cols, max_rows=max_rows)
    return _plot_image(data, cols=cols, rows=rows), cols


def _plot_image(data: bytes, *, cols: int, rows: int) -> list[str]:
    import tempfile

    handle, path = tempfile.mkstemp(prefix="smartpipe-thumb-")
    try:
        with os.fdopen(handle, "wb") as sink:
            sink.write(data)
        import plotext

        plotext.clear_figure()
        plotext.plot_size(cols, rows)
        plotext.image_plot(path, fast=True)
        canvas = plotext.build().rstrip("\n")
        plotext.clear_figure()
        return canvas.split("\n")
    finally:
        os.unlink(path)


# --- audio --------------------------------------------------------------------------


def _waveform(part: AudioData, *, buckets: int) -> list[str]:
    import subprocess
    import tempfile

    from smartpipe.parsing.extract import ffmpeg_exe

    exe = ffmpeg_exe()
    handle, path = tempfile.mkstemp(prefix="smartpipe-wave-")
    try:
        with os.fdopen(handle, "wb") as sink:
            sink.write(part.data)
        decoded = subprocess.run(
            [
                exe,
                "-loglevel",
                "error",
                "-i",
                path,
                "-f",
                "s16le",
                "-ac",
                "1",
                "-ar",
                str(_WAVE_RATE),
                "-t",
                str(_DECODE_CAP_SECONDS),
                "-",
            ],
            check=False,
            capture_output=True,
        )
    finally:
        os.unlink(path)
    if decoded.returncode != 0 or not decoded.stdout:
        detail = decoded.stderr.decode(errors="replace").strip().splitlines()
        raise ItemError(
            f"ffmpeg couldn't decode this audio ({detail[-1] if detail else 'no PCM out'})"
        )
    peaks = peak_envelope(_pcm_samples(decoded.stdout), buckets)
    return _plot_wave(peaks)


def _pcm_samples(pcm: bytes) -> Sequence[int]:
    import array
    import sys

    samples = array.array("h")
    usable = len(pcm) - len(pcm) % samples.itemsize
    samples.frombytes(pcm[:usable])
    if sys.byteorder == "big":  # pragma: no cover — s16LE bytes on a big-endian host
        samples.byteswap()
    return samples


def _plot_wave(peaks: Sequence[float]) -> list[str]:
    import plotext

    plotext.clear_figure()
    plotext.theme("clear")
    positions = list(range(len(peaks)))
    plotext.bar(positions, list(peaks), color="cyan", width=1.0)
    plotext.bar(positions, [-peak for peak in peaks], color="cyan", width=1.0)
    plotext.plot_size(len(peaks), _WAVE_ROWS)
    plotext.xticks([])
    plotext.yticks([])
    plotext.xaxes(False, False)
    plotext.yaxes(False, False)
    canvas = plotext.build().rstrip("\n")
    plotext.clear_figure()
    return canvas.split("\n")


def _clip_seconds(part: AudioData) -> float | None:
    from smartpipe.io.metering import clip_seconds

    return clip_seconds(part.data, part.mime)


# --- video --------------------------------------------------------------------------


def _film_strip(part: VideoData) -> tuple[list[str], float]:
    import shutil
    import subprocess
    import tempfile

    from smartpipe.parsing.extract import ffmpeg_exe, ffprobe_duration

    exe = ffmpeg_exe()
    workdir = tempfile.mkdtemp(prefix="smartpipe-strip-")
    try:
        source = os.path.join(workdir, "source")
        with open(source, "wb") as sink:
            sink.write(part.data)
        seconds = ffprobe_duration(exe, source)
        frames: list[bytes] = []
        for position, offset in enumerate(strip_seconds(seconds)):
            target = os.path.join(workdir, f"frame-{position}.jpg")
            grabbed = subprocess.run(
                [
                    exe,
                    "-loglevel",
                    "error",
                    "-ss",
                    f"{offset:.3f}",
                    "-i",
                    source,
                    "-frames:v",
                    "1",
                    target,
                ],
                check=False,
                capture_output=True,
            )
            if grabbed.returncode == 0 and os.path.exists(target):
                with open(target, "rb") as image:
                    frames.append(image.read())
    finally:
        shutil.rmtree(workdir, ignore_errors=True)
    if not frames:
        raise ItemError("ffmpeg produced no frames from this video")
    thumbs = [_thumbnail(frame, max_cols=_FRAME_COLS, max_rows=_FRAME_ROWS) for frame in frames]
    return list(join_columns(thumbs)), seconds


# --- the play "button" --------------------------------------------------------------


def _play(
    record: Mapping[str, object], part: MediaData, seconds: float | None, *, color: bool
) -> list[str]:
    source = _source_file(record)
    if source is None:
        return []  # bytes-only media (the __media transport): no link, never a temp file
    line = play_line(
        url=source.as_uri(), path=str(source), seconds=seconds, size=len(part.data), color=color
    )
    assert line is not None, "play_line with a url and path always renders"
    return [line]


def _source_file(record: Mapping[str, object]) -> Path | None:
    """The ``__source`` spine's path when it names a file that still exists —
    resolved so ``as_uri`` gets the absolute form."""
    from pathlib import Path

    from smartpipe.core.jsontools import as_record

    source = as_record(record.get("__source"))
    named = source.get("path") if source is not None else None
    if not isinstance(named, str) or named in ("", "-"):
        return None
    candidate = Path(named)
    return candidate.resolve() if candidate.is_file() else None
