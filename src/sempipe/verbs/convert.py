"""Modality → text conversion for embedding and text verbs (D27/D33).

One ladder per modality, one cost fence: a LOCAL chat model converts for free,
automatically; a cloud model converts only behind ``--allow-captions``; whisper
is audio's always-there fallback. Every conversion is a per-row degraded note.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sempipe.core.errors import ItemError
from sempipe.models.base import AudioData, CompletionRequest, ImageData

if TYPE_CHECKING:
    from sempipe.io.diagnostics import DegradationLog
    from sempipe.models.base import ChatModel

__all__ = ["IMAGE_NEEDS_CAPTION", "Converter", "make_converter"]

AUDIO_TO_TEXT_SYSTEM = (
    "You convert audio to text for search indexing. If the audio contains "
    "speech, transcribe it verbatim. If it does not, describe the sound in one "
    "or two factual sentences. Reply with only the transcript or description."
)

IMAGE_TO_TEXT_SYSTEM = (
    "You describe images for search indexing. Describe the image factually in "
    "two or three sentences, including any visible text verbatim. Reply with "
    "only the description."
)

IMAGE_NEEDS_CAPTION = (
    "image needs a description to embed — a local vision model does it free "
    "(profile 'local'), or opt into paid captions: --allow-captions"
)

_CONVERT_MAX_TOKENS = 512


@dataclass(frozen=True, slots=True)
class Converter:
    """The per-run conversion policy: which chat model (if any) may convert."""

    chat: ChatModel | None  # None: no model resolvable — lower rungs only
    allow_paid: bool  # --allow-captions
    log: DegradationLog

    def _model_may_convert(self) -> bool:
        if self.chat is None:
            return False
        return self.chat.ref.provider == "ollama" or self.allow_paid

    def _rung_name(self) -> str:
        assert self.chat is not None
        return f"{self.chat.ref.provider}/{self.chat.ref.name}"

    async def audio_to_text(self, audio: AudioData, where: str) -> str:
        """LLM rung (capability by attempt) → whisper → the two-fix skip."""
        if self._model_may_convert():
            assert self.chat is not None
            try:
                text = await self.chat.complete(
                    CompletionRequest(
                        system=AUDIO_TO_TEXT_SYSTEM,
                        user="Convert this audio to text.",
                        media=(audio,),
                        max_tokens=_CONVERT_MAX_TOKENS,
                    )
                )
            except ItemError:
                text = None  # the model can't hear — fall through to whisper
            if text is not None and text.strip():
                self.log.note(where, "audio → text", f"heard by {self._rung_name()}")
                return text.strip()
        import asyncio

        from sempipe.verbs.common import transcribe

        transcript = await asyncio.to_thread(transcribe, audio)
        import os

        from sempipe.parsing.extract import whisper_size

        self.log.note(where, "audio → text", f"whisper {whisper_size(os.environ)}")
        return transcript

    async def image_to_text(self, image: ImageData, where: str) -> str:
        """LLM rung or nothing — there is no free non-LLM rung for images."""
        if not self._model_may_convert():
            raise ItemError(IMAGE_NEEDS_CAPTION)
        assert self.chat is not None
        text = await self.chat.complete(
            CompletionRequest(
                system=IMAGE_TO_TEXT_SYSTEM,
                user="Describe this image.",
                media=(image,),
                max_tokens=_CONVERT_MAX_TOKENS,
            )
        )
        self.log.note(where, "image → text", f"described by {self._rung_name()}")
        return text.strip()


def make_converter(chat: ChatModel | None, *, allow_paid: bool, log: DegradationLog) -> Converter:
    return Converter(chat=chat, allow_paid=allow_paid, log=log)
