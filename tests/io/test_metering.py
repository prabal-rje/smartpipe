"""Run telemetry (D40): exact units, honest formatting, silent when empty."""

from __future__ import annotations

import io
import wave

import pytest

from smartpipe.io import metering
from smartpipe.models.base import AudioData, ImageData


@pytest.fixture(autouse=True)
def fresh_meter() -> None:
    metering.reset()


def _wav_bytes(seconds: float, rate: int = 8_000) -> bytes:
    buffer = io.BytesIO()
    with wave.open(buffer, "wb") as clip:
        clip.setnchannels(1)
        clip.setsampwidth(2)
        clip.setframerate(rate)
        clip.writeframes(b"\x00\x00" * int(rate * seconds))
    return buffer.getvalue()


def test_tokens_accumulate_and_format() -> None:
    metering.add_tokens(tokens_in=2_100_000, tokens_out=340_000)
    assert metering.status_segment() == "↑2.1M ↓340.0k tok"
    assert metering.receipt() == "run: 2.1M in · 340.0k out tokens"


def test_wav_duration_is_read_from_the_header() -> None:
    metering.add_request_media((AudioData(_wav_bytes(83.0), "audio/wav"),))
    view = metering.snapshot()
    assert 82.5 <= view.audio_seconds <= 83.5
    receipt = metering.receipt()
    assert receipt is not None and "1m23s" in receipt


def test_malformed_wav_is_bytes_only_never_a_crash() -> None:
    metering.add_request_media((AudioData(b"RIFFgarbage", "audio/wav"),))
    view = metering.snapshot()
    assert view.audio_seconds == 0.0
    assert view.media_bytes["audio"] == len(b"RIFFgarbage")


def test_media_kinds_split_in_the_receipt() -> None:
    metering.add_tokens(tokens_in=10)
    metering.add_request_media((ImageData(b"x" * 2_097_152, "image/png"),))
    metering.add_conversion()
    receipt = metering.receipt()
    assert receipt is not None
    assert "2.0 MB images (1)" in receipt
    assert "1 paid conversions" in receipt


def test_empty_meter_is_silent() -> None:
    assert metering.status_segment() == ""
    assert metering.receipt() is None


# --- clip_seconds (D26 v2: the duration probe behind media_tokens) ---------------


def test_clip_seconds_reads_wav_headers_pure() -> None:
    seconds = metering.clip_seconds(_wav_bytes(83.0), "audio/wav")
    assert seconds is not None
    assert 82.5 <= seconds <= 83.5


def test_clip_seconds_asks_ffmpeg_for_other_containers(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """A fake ffmpeg that prints a Duration banner — hermetic, no real codec."""
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    fake = tmp_path / "ffmpeg"
    fake.write_text('#!/bin/sh\necho "Duration: 00:01:23.00" >&2\n', encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(metering, "_ffmpeg_exe", lambda: str(fake))
    assert metering.clip_seconds(b"opus-ish bytes", "audio/ogg") == 83.0


def test_clip_seconds_is_none_when_ffmpeg_is_missing(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(metering, "_ffmpeg_exe", lambda: None)
    assert metering.clip_seconds(b"whatever", "audio/mpeg") is None


def test_ffmpeg_discovery_failure_degrades_to_none(monkeypatch: pytest.MonkeyPatch) -> None:
    from smartpipe.core.errors import ItemError
    from smartpipe.parsing import extract

    def missing() -> str:
        raise ItemError("ffmpeg is unavailable")

    monkeypatch.setattr(extract, "ffmpeg_exe", missing)
    assert metering.clip_seconds(b"opus bytes", "audio/ogg") is None


def test_clip_seconds_is_none_when_ffmpeg_cannot_read_the_clip(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    fake = tmp_path / "ffmpeg"
    fake.write_text('#!/bin/sh\necho "Invalid data" >&2\nexit 1\n', encoding="utf-8")
    fake.chmod(0o755)
    monkeypatch.setattr(metering, "_ffmpeg_exe", lambda: str(fake))
    assert metering.clip_seconds(b"garbage", "video/mp4") is None
