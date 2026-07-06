"""Minimal local stub for the [audio] extra (no upstream py.typed).

Covers exactly the surface sempipe touches; extend only alongside real usage.
"""

from collections.abc import Iterable
from typing import BinaryIO

class Segment:
    text: str

class TranscriptionInfo: ...

class WhisperModel:
    def __init__(
        self, model_size_or_path: str, *, device: str = ..., compute_type: str = ...
    ) -> None: ...
    def transcribe(self, audio: str | BinaryIO) -> tuple[Iterable[Segment], TranscriptionInfo]: ...
