"""Model resolution chains (plan/architecture.md "Provider abstraction").

Chat:  flag > SEMPIPE_MODEL > config.model > ollama autodetect > NO_MODEL screen.
Embed: flag > SEMPIPE_EMBED_MODEL > config.embed_model > nomic-embed-text.

The ollama probe is injected as a first-class async function so this module has
no I/O of its own — the container passes the real probe, tests pass a fake.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from sempipe.cli import screens
from sempipe.core.errors import SetupFault
from sempipe.models.base import ModelRef, parse_model_ref

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from sempipe.config.store import Config

__all__ = ["Resolved", "resolve_chat_ref", "resolve_embed_ref"]

_DEFAULT_EMBED_MODEL = "nomic-embed-text"


@dataclass(frozen=True, slots=True)
class Resolved:
    ref: ModelRef
    notice: str | None = None


async def resolve_chat_ref(
    flag: str | None,
    env: Mapping[str, str],
    config: Config,
    probe: Callable[[], Awaitable[tuple[str, ...] | None]],
) -> Resolved:
    explicit = _first_configured(flag, env.get("SEMPIPE_MODEL"), config.model)
    if explicit is not None:
        return Resolved(parse_model_ref(explicit))
    names = await probe()
    chosen = _first_chat_model(names)
    if chosen is None:
        raise SetupFault(screens.NO_MODEL)
    notice = (
        f"using ollama/{chosen} (no model configured — "
        f"pin one with: sempipe config model ollama/{chosen})"
    )
    return Resolved(ModelRef(provider="ollama", name=chosen), notice=notice)


def resolve_embed_ref(flag: str | None, env: Mapping[str, str], config: Config) -> ModelRef:
    explicit = _first_configured(flag, env.get("SEMPIPE_EMBED_MODEL"), config.embed_model)
    return parse_model_ref(explicit if explicit is not None else _DEFAULT_EMBED_MODEL)


def _first_configured(*candidates: str | None) -> str | None:
    for candidate in candidates:
        if candidate is not None and candidate.strip():
            return candidate.strip()
    return None


def _first_chat_model(names: tuple[str, ...] | None) -> str | None:
    if not names:
        return None
    return next((name for name in names if "embed" not in name.lower()), None)
