"""Provider-neutral model contracts (plan/architecture.md "Provider abstraction").

Routing rule (plan/decisions.md D04, refined): ``provider/name`` is explicit for
the four known providers; bare names route by shape — ``claude-*`` → anthropic,
``gpt-*``/o-series/``text-embedding-*`` → openai, the Mistral family prefixes
(``mistral-``, ``codestral-``, ``pixtral-`` …) → mistral, everything else → ollama.
Unknown ``x/y`` prefixes are NOT errors: Ollama supports namespaced model names
(``someuser/model:tag``, ``hf.co/org/model``), so they route to ollama whole —
which is also why ``hf.co/mistralai/...`` must never be hijacked by the bare-name
prefixes (the slash form wins first).
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from sempipe.core.errors import UsageFault

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "AudioData",
    "ChatModel",
    "CompletionRequest",
    "EmbeddingModel",
    "ImageData",
    "MediaData",
    "ModelRef",
    "Provider",
    "parse_model_ref",
]

Provider = Literal["ollama", "openai", "anthropic", "mistral", "gemini", "openrouter"]

_OPENAI_PREFIXES = ("gpt-", "chatgpt-", "text-embedding-")
_OPENAI_O_SERIES = re.compile(r"o\d")
_MISTRAL_PREFIXES = (
    "mistral-",  # also covers mistral-embed
    "ministral-",
    "codestral-",
    "magistral-",
    "devstral-",
    "pixtral-",
    "voxtral-",  # the audio family — found missing by the live smoke
    "open-mistral-",
    "open-mixtral-",
)


@dataclass(frozen=True, slots=True)
class ImageData:
    data: bytes
    mime: str


@dataclass(frozen=True, slots=True)
class AudioData:
    data: bytes
    mime: str  # audio/mpeg · audio/wav · audio/mp4 · audio/ogg · audio/flac


# The D20 union: ONE optional media field on Item/CompletionRequest, dispatched
# with match + assert_never. VideoData is reserved, not added — no wired provider
# carries video on our wires (capability follows wire).
MediaData = ImageData | AudioData


@dataclass(frozen=True, slots=True)
class ModelRef:
    provider: Provider
    name: str  # passed through to the backend verbatim — sempipe keeps no registry

    def __str__(self) -> str:
        return f"{self.provider}/{self.name}"


@dataclass(frozen=True, slots=True)
class CompletionRequest:
    system: str | None
    user: str
    json_schema: Mapping[str, object] | None = None  # provider-native structured output
    max_tokens: int = 8192
    media: tuple[MediaData, ...] = ()  # vision/audio (bytes + mime; D20 union)


class ChatModel(Protocol):
    # A read-only property (not a bare attribute) so frozen-dataclass adapters,
    # whose fields pyright treats as read-only, structurally satisfy the Protocol.
    @property
    def ref(self) -> ModelRef: ...

    async def complete(self, request: CompletionRequest) -> str: ...


class EmbeddingModel(Protocol):
    @property
    def ref(self) -> ModelRef: ...

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


def parse_model_ref(text: str) -> ModelRef:
    cleaned = text.strip()
    if not cleaned:
        raise UsageFault("no model given — try: --model ollama/qwen3:8b")
    prefix, slash, rest = cleaned.partition("/")
    if slash:
        match prefix:
            case "ollama" | "openai" | "anthropic" | "mistral" | "gemini" | "openrouter":
                if not rest:
                    raise UsageFault(f"model '{cleaned}' is missing a name after '{prefix}/'")
                return ModelRef(provider=prefix, name=rest)
            case _:
                pass  # a namespaced ollama model name — fall through to shape routing
    if cleaned.startswith("claude"):
        return ModelRef(provider="anthropic", name=cleaned)
    if cleaned.startswith(_OPENAI_PREFIXES) or _OPENAI_O_SERIES.match(cleaned):
        return ModelRef(provider="openai", name=cleaned)
    if not slash and cleaned.startswith(_MISTRAL_PREFIXES):
        return ModelRef(provider="mistral", name=cleaned)
    if not slash and cleaned.startswith("gemini-"):
        return ModelRef(provider="gemini", name=cleaned)
    # openrouter is explicit-only: its names ARE other vendors' names — a bare
    # prefix would hijack every routing rule above (D24 commit)
    return ModelRef(provider="ollama", name=cleaned)
