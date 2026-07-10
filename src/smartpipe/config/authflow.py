"""The credential door's decisions and flows (``smartpipe auth login/list/logout``).

Everything here takes its I/O as injected callables (secret prompt, chooser,
say, checker, store) so the whole door is unit-testable without a terminal -
the click wiring in ``cli/auth_cmd`` supplies the real prompts. Key material
passes through as function arguments and is never echoed, logged, or stored
anywhere but the 0600 key store.

OpenAI is TWO entries on purpose: the API-key wire and the ChatGPT-login wire
are different transports with different capabilities (the login wire has no
embeddings), so they connect, list, and log out independently.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Literal, assert_never

from smartpipe.config.credentials import KEY_ENVS, key_source, mask_key
from smartpipe.models.keycheck import KeyRejected, KeyUnchecked, KeyValid

if TYPE_CHECKING:
    from collections.abc import Awaitable, Callable, Mapping

    from smartpipe.models.keycheck import KeyVerdict

__all__ = [
    "AUTH_ENTRIES",
    "AuthEntry",
    "auth_entry",
    "connect_api_key",
    "list_rows",
    "login_badge",
    "login_menu_labels",
    "logout_candidates",
]


@dataclass(frozen=True, slots=True)
class AuthEntry:
    """One row of the credential door - what to call it, how it connects."""

    entry_id: str  # what the CLI accepts and logout names
    provider: str  # the store / KEY_ENVS key
    label: str  # the menu row
    kind: Literal["api", "oauth"]
    key_url: str = ""  # where to create a key (api entries)
    aside: str = ""  # a dim qualifier ("embeddings only")
    aliases: tuple[str, ...] = ()


AUTH_ENTRIES: tuple[AuthEntry, ...] = (
    AuthEntry(
        "openai-api", "openai", "openai (API key)", "api", "https://platform.openai.com/api-keys"
    ),
    AuthEntry("openai", "openai", "openai (ChatGPT login)", "oauth", aliases=("chatgpt",)),
    AuthEntry(
        "anthropic", "anthropic", "anthropic", "api", "https://console.anthropic.com/settings/keys"
    ),
    AuthEntry("gemini", "gemini", "gemini", "api", "https://aistudio.google.com/app/apikey"),
    AuthEntry("mistral", "mistral", "mistral", "api", "https://console.mistral.ai/api-keys"),
    AuthEntry("openrouter", "openrouter", "openrouter", "api", "https://openrouter.ai/keys"),
    AuthEntry("jina", "jina", "jina", "api", "https://jina.ai", aside="embeddings only"),
)


def auth_entry(name: str) -> AuthEntry | None:
    cleaned = name.strip().lower()
    return next(
        (entry for entry in AUTH_ENTRIES if cleaned == entry.entry_id or cleaned in entry.aliases),
        None,
    )


def login_badge(
    entry: AuthEntry,
    *,
    env: Mapping[str, str],
    stored: Mapping[str, str],
    logged_in: bool,
) -> str:
    """What the login menu shows next to a row - connected, and via what."""
    if entry.kind == "oauth":
        return "✓ ChatGPT" if logged_in else "needs login"
    match key_source(env, stored, entry.provider):
        case "env":
            return "✓ key (env)"
        case "stored":
            return "✓ key (stored)"
        case None:
            return "needs key"
        case _ as unreachable:  # pragma: no cover - pyright proves exhaustiveness
            assert_never(unreachable)


def login_menu_labels(
    *, env: Mapping[str, str], stored: Mapping[str, str], logged_in: bool
) -> tuple[str, ...]:
    def row(entry: AuthEntry) -> str:
        badge = login_badge(entry, env=env, stored=stored, logged_in=logged_in)
        aside = f"   ({entry.aside})" if entry.aside else ""
        return f"{entry.label:<24}{badge}{aside}"

    return tuple(row(entry) for entry in AUTH_ENTRIES)


async def connect_api_key(
    entry: AuthEntry,
    *,
    secret: Callable[[str], str],
    choose: Callable[[str, tuple[str, ...], int], int | None],
    say: Callable[[str], None],
    check: Callable[[str], Awaitable[KeyVerdict]],
    store: Callable[[str], None],
    store_display: str,
) -> bool:
    """The key path: URL line, masked prompt, live check, three-way on failure.

    Returns True when a key was stored. The transcript never contains the key.
    """
    say(f"  Create a key: {entry.key_url}")
    prompt = f"Enter your {entry.provider.upper()} API key"
    answer = secret(prompt)
    while True:
        key = answer.strip()
        if not key:
            say("  nothing entered - skipped")
            return False
        verdict = await check(key)
        match verdict:
            case KeyValid():
                say(f"  ✓ the key works ({entry.provider} answered)")
            case KeyUnchecked(reason=reason):
                say(f"  storing unchecked - {reason}")
            case KeyRejected(detail=detail):
                say(f"  ✗ the check failed ({detail})")
                picked = choose(
                    "The key didn't validate:",
                    (
                        "retry - paste it again",
                        "store anyway - the provider may be having a bad minute",
                        "skip - store nothing",
                    ),
                    0,
                )
                if picked == 0:
                    answer = secret(prompt)
                    continue
                if picked != 1:
                    say("  skipped - nothing stored")
                    return False
            case _ as unreachable:  # pragma: no cover - pyright proves exhaustiveness
                assert_never(unreachable)
        store(key)
        say(
            f"  ✓ stored at {store_display} (owner-only) - "
            f"remove with: smartpipe auth logout {entry.entry_id}"
        )
        return True


def list_rows(
    *,
    env: Mapping[str, str],
    stored: Mapping[str, str],
    oauth_account: str | None,
) -> tuple[str, ...]:
    """``auth list``'s lines: provider, type, MASKED key, which source is live."""
    rows: list[str] = []
    for entry in AUTH_ENTRIES:
        if entry.kind == "oauth":
            if oauth_account is not None:
                rows.append(_row("openai", "ChatGPT login", f"account {oauth_account}", "stored"))
            continue
        source = key_source(env, stored, entry.provider)
        if source is None:
            continue
        if source == "env":
            var = next(v for v in KEY_ENVS[entry.provider] if env.get(v, "").strip())
            origin = f"env {var}"
            if entry.provider in stored:
                origin += " (stored key shadowed)"
            rows.append(_row(entry.provider, "API key", mask_key(env[var].strip()), origin))
        else:
            rows.append(_row(entry.provider, "API key", mask_key(stored[entry.provider]), "stored"))
    return tuple(rows)


def _row(provider: str, kind: str, masked: str, origin: str) -> str:
    return f"{provider:<12}{kind:<15}{masked:<18}{origin}"


def logout_candidates(*, stored: Mapping[str, str], logged_in: bool) -> tuple[AuthEntry, ...]:
    """What ``auth logout`` can actually remove - stored things only (env keys
    belong to the shell, not to smartpipe)."""

    def removable(entry: AuthEntry) -> bool:
        if entry.kind == "oauth":
            return logged_in
        return entry.provider in stored

    return tuple(entry for entry in AUTH_ENTRIES if removable(entry))
