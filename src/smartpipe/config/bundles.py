"""One-shot setup bundles for ``smartpipe use TARGET`` (item 30).

Pure decisions only: what a target means (a provider name or a model ref),
which COMPLETE bundle it implies (chat model + the owner-ratified embed
pairing + the captions consent a cloud pick carries), and the refusal screen
when a credential is absent — never a partial stamp. The terminal, the key
store, and the Ollama probe live in ``cli/config_cmd``.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.config.picker import ollama_chat_tags, paired_embed, preferred_index
from smartpipe.models.base import parse_model_ref

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["PROVIDERS", "Bundle", "Refusal", "resolve_bundle", "target_provider"]

PROVIDERS = ("ollama", "openai", "gemini", "anthropic", "mistral", "openrouter")

_CLOUD = frozenset(("openai", "gemini", "anthropic", "mistral", "openrouter"))

# The sensible chat default a provider one-shot stamps. gpt-5.4-mini needs the
# key wire — Codex (ChatGPT-login) accounts reject it, so the login path below
# stamps the plan-served family instead.
_DEFAULT_CHAT: Mapping[str, str] = {
    "openai": "openai/gpt-5.4-mini",
    "gemini": "gemini/gemini-3.1-flash-lite",
    "anthropic": "anthropic/claude-opus-4-8",
    "mistral": "mistral/mistral-small-latest",
    "openrouter": "openrouter/anthropic/claude-sonnet-5",
}
_OPENAI_LOGIN_CHAT = "openai/gpt-5.4"
_OLLAMA_SUGGESTED_PULL = "gemma-4-e2b"  # multimodal, small — the pull hint when none installed


@dataclass(frozen=True, slots=True)
class Bundle:
    """A complete, coherent stamp: chat + embed pairing + consent, disclosed."""

    provider: str
    model: str
    embed_model: str | None  # None = leave the embedding ladder alone
    allow_captions: bool  # True on a cloud pick (D35) — the pick IS the consent
    notes: tuple[str, ...] = ()  # asides printed with the stamp


@dataclass(frozen=True, slots=True)
class Refusal:
    """Why no bundle can be stamped — a full screen with its own fix."""

    screen: str


def target_provider(target: str) -> str:
    """Which provider a ``use`` target implies — a bare provider name, or the
    model ref's routing (UsageFault propagates for unparseable refs)."""
    if target in PROVIDERS:
        return target
    return parse_model_ref(target).provider


def resolve_bundle(
    target: str,
    *,
    env: Mapping[str, str],
    login: bool,
    ollama_tags: tuple[str, ...] | None,
) -> Bundle | Refusal:
    """The bundle a target stamps, or the refusal that explains itself.

    ``env`` must already carry the ``auth login`` store's overlay;
    ``ollama_tags`` is the live daemon probe (None = nothing listening) and
    only consulted for ollama targets."""
    provider = target_provider(target)
    explicit = None if target in PROVIDERS else str(parse_model_ref(target))
    if provider in ("local", "jina"):
        return Refusal(
            f"error: '{target}' is an embedding model, not a chat model\n"
            "  'smartpipe use' stamps a chat model plus its paired embedder.\n"
            "  Pick a chat model (e.g. smartpipe use gemini) — or run: smartpipe use"
        )
    if explicit is not None and _embeds(explicit):
        return Refusal(
            f"error: '{explicit}' is an embedding model, not a chat model\n"
            "  'smartpipe use' stamps a chat model plus its paired embedder.\n"
            "  Pick a chat model (e.g. smartpipe use gemini) — or run: smartpipe use"
        )
    if provider == "ollama":
        return _ollama_bundle(target, explicit, ollama_tags)
    return _cloud_bundle(target, provider, explicit, env=env, login=login)


def _ollama_bundle(
    target: str, explicit: str | None, tags: tuple[str, ...] | None
) -> Bundle | Refusal:
    if tags is None:
        return Refusal(
            "error: can't reach ollama — nothing is listening\n"
            "  Install it from https://ollama.com, then: ollama serve\n"
            f"  Then rerun: smartpipe use {target}"
        )
    if explicit is None:
        chats = ollama_chat_tags(tags)
        if not chats:
            return Refusal(
                "error: ollama is running, but no chat model is installed\n"
                f"  Pull one first: ollama pull {_OLLAMA_SUGGESTED_PULL}\n"
                f"  Then rerun: smartpipe use {target}"
            )
        model = f"ollama/{chats[preferred_index(chats)]}"
    else:
        model = explicit
    pair = paired_embed("ollama", tags)
    assert pair is not None, "ollama always pairs (the default embed tag)"
    installed = any("embed" in tag.lower() for tag in tags)
    notes = () if installed else (f"pull the embedder first: ollama pull {_embed_name(pair)}",)
    return Bundle(
        provider="ollama", model=model, embed_model=pair, allow_captions=False, notes=notes
    )


def _cloud_bundle(
    target: str,
    provider: str,
    explicit: str | None,
    *,
    env: Mapping[str, str],
    login: bool,
) -> Bundle | Refusal:
    from smartpipe.config.credentials import KEY_ENVS

    has_key = any(env.get(var, "").strip() for var in KEY_ENVS.get(provider, ()))
    if not has_key and provider == "openai" and login:
        # the ChatGPT wire: plan-served chat, no embeddings endpoint
        return Bundle(
            provider="openai",
            model=explicit or _OPENAI_LOGIN_CHAT,
            embed_model=None,
            allow_captions=True,
            notes=("ChatGPT login wire — embeddings stay local (the login wire has none)",),
        )
    if not has_key:
        return Refusal(_needs_credential(target, provider))
    pair = paired_embed(provider, None)
    notes = (
        ()
        if pair is not None
        else (f"embeddings stay on the built-in local default ({provider} has no embedding wire)",)
    )
    return Bundle(
        provider=provider,
        model=explicit or _DEFAULT_CHAT[provider],
        embed_model=pair,
        allow_captions=True,
        notes=notes,
    )


def _needs_credential(target: str, provider: str) -> str:
    if provider == "openai":
        return (
            "error: openai needs a credential, and none was found\n"
            "  Connect it first: smartpipe auth login openai-api   (API key)\n"
            "                or: smartpipe auth login openai       (ChatGPT plan)\n"
            f"  Then rerun: smartpipe use {target}"
        )
    return (
        f"error: {provider} needs an API key, and none was found\n"
        f"  Connect it first: smartpipe auth login {provider}\n"
        f"  Then rerun: smartpipe use {target}"
    )


def _embeds(model: str) -> bool:
    """The same fence the staged flow's backup picker uses — a chat stamp must chat."""
    ref = parse_model_ref(model)
    return ref.provider in ("local", "jina") or "embed" in ref.name.lower()


def _embed_name(pair: str) -> str:
    return pair.rsplit("/", 1)[-1]
