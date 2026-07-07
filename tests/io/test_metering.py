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
