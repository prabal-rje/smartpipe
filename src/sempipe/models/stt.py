"""Remote speech-to-text (D39/05): the ``stt-model`` role's wire.

A configured transcriber signals wanting VERBATIM text — LLM hearing
paraphrases; whisper transcribes. v1 implements the openai wire
(``/v1/audio/transcriptions``); the role key accepts ``provider/model`` so
more wires can land behind the same seam.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING

import httpx

from sempipe.core.errors import ItemError, SetupFault
from sempipe.io import metering
from sempipe.models.http_support import is_retryable_http, retry_after_seconds
from sempipe.models.retry import RetryPolicy, with_retries

if TYPE_CHECKING:
    from sempipe.models.base import AudioData, ModelRef

__all__ = ["RemoteTranscriber"]

_EXTENSIONS = {
    "audio/mpeg": "mp3",
    "audio/mp4": "m4a",
    "audio/wav": "wav",
    "audio/x-wav": "wav",
    "audio/ogg": "ogg",
    "audio/webm": "webm",
    "audio/flac": "flac",
}


@dataclass(frozen=True, slots=True)
class RemoteTranscriber:
    ref: ModelRef
    client: httpx.AsyncClient
    api_key: str
    base_url: str = "https://api.openai.com"
    retry: RetryPolicy = field(default_factory=RetryPolicy)

    async def transcribe(self, audio: AudioData) -> str:
        extension = _EXTENSIONS.get(audio.mime, "mp3")
        files = {"file": (f"audio.{extension}", audio.data, audio.mime)}
        data = {"model": self.ref.name, "response_format": "text"}
        headers = {"Authorization": f"Bearer {self.api_key}"}
        metering.add_request_media((audio,))

        async def attempt() -> str:
            response = await self.client.post(
                f"{self.base_url}/v1/audio/transcriptions",
                files=files,
                data=data,
                headers=headers,
            )
            response.raise_for_status()
            return response.text

        try:
            return (
                await with_retries(
                    self.retry,
                    attempt,
                    is_retryable=is_retryable_http,
                    delay_hint=retry_after_seconds,
                )
            ).strip()
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            if status in (401, 403):
                raise SetupFault(
                    "error: the STT wire rejected the API key\n"
                    "  Remote transcription uses OPENAI_API_KEY — set it and retry."
                ) from exc
            raise ItemError(f"stt error {status}: {exc.response.text[:200]}") from exc
        except httpx.HTTPError as exc:
            raise ItemError(f"stt request failed ({exc})") from exc
