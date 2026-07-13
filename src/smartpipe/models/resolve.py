"""Model resolution chains (plan/architecture.md "Provider abstraction").

Chat:  flag > SMARTPIPE_MODEL > config.model > ollama autodetect > NO_MODEL screen.
Embed: flag > SMARTPIPE_EMBED_MODEL > config.embed_model > nomic-embed-text.
STT:   SMARTPIPE_STT_MODEL > config.stt-model > the auto-matrix (pure, shared
       by the container, doctor, and --probe so the ladder story never forks).

The ollama probe is injected as a first-class async function so this module has
no I/O of its own — the container passes the real probe, tests pass a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal

from smartpipe.cli import screens
from smartpipe.core.errors import SetupFault
from smartpipe.models.base import ModelRef, parse_model_ref

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from smartpipe.config.store import Config

__all__ = [
    "LOCAL_STT",
    "Resolved",
    "SttResolution",
    "resolve_chat_ref",
    "resolve_embed_ref",
    "resolve_stt",
]

# The stt-model sentinel that pins on-device whisper (never parsed as a ref —
# parse_model_ref("local") would misread it as an ollama model name).
LOCAL_STT = "local"


def _default_embed_model() -> str:
    """D44: on-device fastembed when its wheels exist (all pythons < 3.14
    today); the Ollama default otherwise — same model family either way."""
    from importlib.util import find_spec

    if find_spec("fastembed") is not None:
        return "local/nomic-embed-text-v1.5"
    return "nomic-embed-text"


@dataclass(frozen=True, slots=True)
class Resolved:
    ref: ModelRef
    notice: str | None = None


@dataclass(frozen=True, slots=True)
class SttResolution:
    """Where audio transcription would go, and why — display and wiring share it."""

    kind: Literal["remote", "local", "ladder"]  # ladder = no rung-0 transcriber
    ref: str | None  # set only for kind == "remote"
    source: Literal["flag", "env", "config", "auto"]


def resolve_stt(
    env: Mapping[str, str],
    stt_model: str | None,
    chat_provider: str | None,
    *,
    flag: str | None = None,
) -> SttResolution:
    """The stt-model role (D39/05), pure: an explicit flag/env/config wins —
    ``local`` pins on-device whisper; otherwise the owner's auto-matrix — an
    openai chat model plus its KEY means whisper-1 (the API supports it;
    ChatGPT-login does not, so OAuth-only stays on the ladder); gemini hears
    natively (no preemption); ollama has no STT (the ladder ends at local
    whisper). ``flag`` (#20: graph's ``--stt-model``) is the highest rung."""
    raw = (flag or "").strip()
    source: Literal["flag", "env", "config", "auto"] = "flag"
    if not raw:
        raw = env.get("SMARTPIPE_STT_MODEL", "").strip()
        source = "env"
    if not raw:
        raw = (stt_model or "").strip()
        source = "config"
    if not raw:
        if chat_provider == "openai" and env.get("OPENAI_API_KEY", "").strip():
            return SttResolution("remote", "openai/whisper-1", "auto")
        return SttResolution("ladder", None, "auto")
    if raw.lower() == LOCAL_STT:
        return SttResolution("local", None, source)
    return SttResolution("remote", raw, source)


async def resolve_chat_ref(
    flag: str | None,
    env: Mapping[str, str],
    config: Config,
    probe: Callable[[], Awaitable[tuple[str, ...] | None]],
) -> Resolved:
    explicit = _first_configured(flag, env.get("SMARTPIPE_MODEL"), config.model)
    if explicit is not None:
        return Resolved(parse_model_ref(explicit))
    names = await probe()
    chosen = _first_chat_model(names)
    if chosen is None:
        raise SetupFault(screens.NO_MODEL)
    notice = (
        f"using ollama/{chosen} (no model configured — pin one with: smartpipe use ollama/{chosen})"
    )
    return Resolved(ModelRef(provider="ollama", name=chosen), notice=notice)


def resolve_embed_ref(flag: str | None, env: Mapping[str, str], config: Config) -> ModelRef:
    explicit = _first_configured(flag, env.get("SMARTPIPE_EMBED_MODEL"), config.embed_model)
    return parse_model_ref(explicit if explicit is not None else _default_embed_model())


def _first_configured(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate is not None and candidate.strip():
            return candidate.strip()
    return None


def _first_chat_model(names: tuple[str, ...] | None) -> str | None:
    if not names:
        return None
    return next((name for name in names if "embed" not in name.lower()), None)
