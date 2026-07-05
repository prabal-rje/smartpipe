"""Provider-neutral model contracts (plan/architecture.md "Provider abstraction").

Routing rule (plan/decisions.md D04, refined): ``provider/name`` is explicit for
the three known providers; bare names route by shape — ``claude-*`` → anthropic,
``gpt-*``/o-series/``text-embedding-*`` → openai, everything else → ollama.
Unknown ``x/y`` prefixes are NOT errors: Ollama supports namespaced model names
(``someuser/model:tag``, ``hf.co/org/model``), so they route to ollama whole.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, Protocol

from sempipe.core.errors import UsageFault

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "ChatModel",
    "CompletionRequest",
    "EmbeddingModel",
    "ModelRef",
    "Provider",
    "parse_model_ref",
]

Provider = Literal["ollama", "openai", "anthropic"]

_OPENAI_PREFIXES = ("gpt-", "chatgpt-", "text-embedding-")
_OPENAI_O_SERIES = re.compile(r"o\d")


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
    images: tuple[bytes, ...] = ()  # vision path (stage 7)


class ChatModel(Protocol):
    ref: ModelRef

    async def complete(self, request: CompletionRequest) -> str: ...


class EmbeddingModel(Protocol):
    ref: ModelRef

    async def embed(self, texts: Sequence[str]) -> tuple[tuple[float, ...], ...]: ...


def parse_model_ref(text: str) -> ModelRef:
    cleaned = text.strip()
    if not cleaned:
        raise UsageFault("no model given — try: --model ollama/qwen3:8b")
    prefix, slash, rest = cleaned.partition("/")
    if slash:
        match prefix:
            case "ollama" | "openai" | "anthropic":
                if not rest:
                    raise UsageFault(f"model '{cleaned}' is missing a name after '{prefix}/'")
                return ModelRef(provider=prefix, name=rest)
            case _:
                pass  # a namespaced ollama model name — fall through to shape routing
    if cleaned.startswith("claude"):
        return ModelRef(provider="anthropic", name=cleaned)
    if cleaned.startswith(_OPENAI_PREFIXES) or _OPENAI_O_SERIES.match(cleaned):
        return ModelRef(provider="openai", name=cleaned)
    return ModelRef(provider="ollama", name=cleaned)
