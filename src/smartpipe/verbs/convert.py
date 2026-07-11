"""Modality → text conversion for embedding and text verbs (D27/D33).

One ladder per modality, one cost fence: a LOCAL chat model converts for free,
automatically; a cloud model converts only behind ``--allow-captions``; whisper
is audio's always-there fallback. Every conversion is a per-row degraded note.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError, is_recoverable_item_error
from smartpipe.engine.schema import validate_and_coerce
from smartpipe.io.readers import OcrIngest
from smartpipe.models.base import AudioData, CompletionRequest, ImageData, VideoData

if TYPE_CHECKING:
    from smartpipe.io.diagnostics import DegradationLog
    from smartpipe.io.items import Item
    from smartpipe.models.base import ChatModel, EmbeddingModel
    from smartpipe.models.ocr import DocumentParser
    from smartpipe.models.stt import Transcriber

__all__ = ["IMAGE_NEEDS_CAPTION", "Converter", "embed_video_halves", "make_converter"]

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

VIDEO_TO_TEXT_SYSTEM = (
    "You convert video to text for search indexing. Describe what is shown in "
    "two or three factual sentences, then transcribe all speech verbatim. "
    "Reply with only the description and transcript."
)

IMAGE_NEEDS_CAPTION = (
    "image needs a description to embed — a local vision model does it free "
    "(smartpipe use ollama), or opt into paid captions: --allow-captions"
)

_CONVERT_MAX_TOKENS = 512
_CAPTION_FRAMES = 4  # the fallback captions a handful, not a filmstrip

_VIDEO_HALVES_SCHEMA: dict[str, object] = {
    "type": "object",
    "properties": {
        "visual": {"type": "string", "description": "what is shown, 2-3 sentences"},
        "transcript": {"type": "string", "description": "all speech verbatim; empty if none"},
    },
    "required": ["visual", "transcript"],
    "additionalProperties": False,
}


@dataclass(frozen=True, slots=True)
class Converter:
    """The per-run conversion policy: which chat model (if any) may convert."""

    chat: ChatModel | None  # None: no model resolvable — lower rungs only
    allow_paid: bool  # --allow-captions
    log: DegradationLog
    stt: Transcriber | None = None  # the stt-model role (D39/05): verbatim rung 0
    ocr: DocumentParser | OcrIngest | None = None  # configured role outranks vision-chat

    def _meter_paid(self) -> None:
        from smartpipe.io import metering

        if self.chat is None or self.chat.ref.provider != "ollama":
            metering.add_conversion()  # cloud conversions bill; local ones don't

    def _model_may_convert(self) -> bool:
        if self.chat is None:
            return False
        return self.chat.ref.provider == "ollama" or self.allow_paid

    def _rung_name(self) -> str:
        assert self.chat is not None
        return f"{self.chat.ref.provider}/{self.chat.ref.name}"

    async def audio_to_text(self, audio: AudioData, where: str) -> str:
        """stt-model rung (verbatim, consent-gated) → LLM rung → whisper →
        the two-fix skip. A configured transcriber runs FIRST: whoever set it
        wants verbatim, and LLM hearing paraphrases (D39/05)."""
        if self.stt is not None and self.allow_paid:
            try:
                transcript = await self.stt.transcribe(audio)
            except ItemError as fault:
                if not is_recoverable_item_error(fault):
                    raise
                transcript = ""  # the wire hiccuped — the ladder continues below
            if transcript:
                # Remote STT meters at its provider boundary. Using the chat
                # ref here misclassified the spend (and could double-count it).
                self.log.note(
                    where,
                    "audio → text",
                    f"transcribed by {self.stt.ref.provider}/{self.stt.ref.name}",
                )
                return transcript
        if self._model_may_convert():
            assert self.chat is not None
            try:
                text = await self.chat.complete(
                    CompletionRequest(
                        system=AUDIO_TO_TEXT_SYSTEM,
                        user="Convert this audio to text.",
                        media=(audio,),
                        max_tokens=_CONVERT_MAX_TOKENS,
                        presence_penalty=0.5,  # prose call — anti-rambling (D35)
                        frequency_penalty=0.5,
                    )
                )
            except ItemError as fault:
                if not is_recoverable_item_error(fault):
                    raise
                text = None  # the model can't hear — fall through to whisper
            if text is not None and text.strip():
                self._meter_paid()
                self.log.note(where, "audio → text", f"heard by {self._rung_name()}")
                return text.strip()
        import asyncio

        from smartpipe.parsing.extract import configured_whisper_size

        transcript = await asyncio.to_thread(_whisper_or_skip, audio)

        self.log.note(where, "audio → text", f"whisper {configured_whisper_size()}")
        return transcript

    async def video_halves(self, video: VideoData, where: str) -> tuple[str | None, str | None]:
        """(visual, speech) — the two halves of a video's meaning (D36).

        Rung 0: ONE watching call returns both via a response schema (gemini
        native accepts; every other wire refuses pre-send, free). Rung 1: frame
        captions + the track through the audio ladder. Rung 2: track only."""
        if self._model_may_convert():
            assert self.chat is not None
            try:
                reply = await self.chat.complete(
                    CompletionRequest(
                        system=VIDEO_TO_TEXT_SYSTEM,
                        user="Convert this video to text.",
                        media=(video,),
                        json_schema=_VIDEO_HALVES_SCHEMA,
                        max_tokens=_CONVERT_MAX_TOKENS,
                    )
                )
                halves = validate_and_coerce(reply, _VIDEO_HALVES_SCHEMA)
                visual = str(halves.get("visual") or "").strip() or None
                speech = str(halves.get("transcript") or "").strip() or None
                if visual or speech:
                    self._meter_paid()
                    self.log.note(where, "video → text", f"watched by {self._rung_name()}")
                    return visual, speech
            except ItemError as fault:
                if not is_recoverable_item_error(fault):
                    raise
        import asyncio

        from smartpipe.parsing.extract import video_to_parts

        parts = await asyncio.to_thread(video_to_parts, video, max_frames=_CAPTION_FRAMES)
        visual = None
        if self._model_may_convert() and parts.frames:
            captions = [await self.image_to_text(frame, where) for frame in parts.frames]
            visual = "\n".join(
                f"[scene {position}] {caption}"
                for position, caption in enumerate(captions, start=1)
            )
        speech = None
        if parts.track is not None:
            speech = await self.audio_to_text(parts.track, where)
        if visual is None and speech is None:
            raise ItemError(
                "this video has no audio track and no model can describe its "
                "frames — map can still see them"
            )
        if visual is None:
            self.log.note(
                where, "video → text", "audio track only; frames dropped — map sees frames"
            )
        return visual, speech

    async def image_to_text(self, image: ImageData, where: str) -> str:
        """ocr-model rung (item 40, when configured — that IS the consent) →
        LLM rung → nothing: there is no free non-LLM rung for images."""
        if self.ocr is not None:
            try:
                read = (
                    await self.ocr.parse_conversion_image(image, where)
                    if isinstance(self.ocr, OcrIngest)
                    else await self.ocr.parse_image(image)
                )
            except ItemError as fault:
                if not is_recoverable_item_error(fault):
                    raise
                read = ""  # the parser hiccuped — the vision-chat rung continues below
            if read.strip():
                self.log.note(where, "image → text", f"parsed by {self.ocr.ref}")
                return read.strip()
        if not self._model_may_convert():
            raise ItemError(IMAGE_NEEDS_CAPTION)
        assert self.chat is not None
        text = await self.chat.complete(
            CompletionRequest(
                system=IMAGE_TO_TEXT_SYSTEM,
                user="Describe this image.",
                media=(image,),
                max_tokens=_CONVERT_MAX_TOKENS,
                presence_penalty=0.5,  # prose call — anti-rambling (D35)
                frequency_penalty=0.5,
            )
        )
        self._meter_paid()
        self.log.note(where, "image → text", f"described by {self._rung_name()}")
        return text.strip()


async def embed_video_halves(
    model: EmbeddingModel,
    item: Item,
    video: VideoData,
    converter: Converter,
) -> tuple[Item, tuple[float, ...]]:
    """D36: a video's vector is the FAIR AVERAGE of its two halves — the visual
    description and the speech transcript embedded separately and mean-pooled
    50/50, so neither drowns the other. Returns (converted item, vector)."""
    from dataclasses import replace

    from smartpipe.engine.chunking import mean_pool
    from smartpipe.io.items import describe_source

    visual, speech = await converter.video_halves(video, describe_source(item.source))
    texts = [part for part in (visual, speech) if part]
    vectors = await model.embed(texts)
    vector = mean_pool(vectors)
    converted = replace(item, text="\n\n".join(texts), media=())
    return converted, vector


AUDIO_NEEDS_TEXT = (
    "audio items need text here — local transcription failed, "
    "and no audio-capable model is configured (try map, or set stt-model)"
)


def _whisper_or_skip(audio: AudioData) -> str:
    """Whisper with the pinned two-fix skip (self-contained: no common import —
    verbs.common imports THIS module, and a cycle turns types Unknown)."""
    from smartpipe.parsing.extract import MissingExtra, transcribe_audio

    try:
        return transcribe_audio(audio)
    except MissingExtra as exc:
        raise ItemError(AUDIO_NEEDS_TEXT) from exc


def make_converter(
    chat: ChatModel | None,
    *,
    allow_paid: bool,
    log: DegradationLog,
    stt: Transcriber | None = None,
    ocr: DocumentParser | OcrIngest | None = None,
) -> Converter:
    return Converter(chat=chat, allow_paid=allow_paid, log=log, stt=stt, ocr=ocr)
