"""Pure decisions behind the provider-first picker (bare ``smartpipe config``).

Everything here is a value-in/value-out function: provider detection, catalog
parsing and filtering, the owner-ratified embed-pairing table, capability
chips, and cache-day math. The terminal, the network, and the clock live in
``cli/config_cmd``, ``models/catalogs``, and ``config/state_cache``.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from smartpipe.core.jsontools import as_items, as_record, as_str

if TYPE_CHECKING:
    from collections.abc import Iterable, Mapping

__all__ = [
    "JINA_EMBED_MODELS",
    "LOCAL_EMBED_MODELS",
    "MENU_CAP",
    "ProbeChip",
    "ProviderStatus",
    "StageEntry",
    "cache_day",
    "capped_catalog",
    "chip_text",
    "detect_providers",
    "embed_pair_allowed",
    "embed_stage_entries",
    "first_embed_tag",
    "has_jina_key",
    "key_stage_entry",
    "model_labels",
    "ocr_stage_rows",
    "ollama_chat_tags",
    "ollama_embed_tags",
    "paired_embed",
    "parse_anthropic_catalog",
    "parse_gemini_catalog",
    "parse_gemini_embed_catalog",
    "parse_mistral_catalog",
    "parse_mistral_embed_catalog",
    "parse_openai_catalog",
    "parse_openai_embed_catalog",
    "parse_openrouter_catalog",
    "preferred_index",
    "stage_labels",
    "text_stage_entries",
]

MENU_CAP = 30  # a menu taller than the terminal helps nobody — typed input covers the rest


@dataclass(frozen=True, slots=True)
class ProviderStatus:
    """One provider's detection verdict, plus the fix when it's not connected."""

    provider: str
    detected: bool
    detail: str  # what detection saw ("API key", "4 local models", …)
    connect_hint: str  # printed dim when undetected — the screen contains its own fix


@dataclass(frozen=True, slots=True)
class ProbeChip:
    """What ``doctor --probe`` PAID to learn about one model (no cache = no claims)."""

    sees: bool
    hears: bool
    ts: float


def detect_providers(
    env: Mapping[str, str],
    *,
    ollama_tags: tuple[str, ...] | None,
    openai_login: bool,
) -> tuple[ProviderStatus, ...]:
    """Every chat-capable provider, detected or not — jina is embeddings-only
    and never appears here (``has_jina_key`` covers its mention)."""

    def has(var: str) -> bool:
        return bool(env.get(var, "").strip())

    openai_bits = " + ".join(
        label
        for label, present in (("API key", has("OPENAI_API_KEY")), ("ChatGPT login", openai_login))
        if present
    )
    gemini_var = next((v for v in ("GEMINI_API_KEY", "GOOGLE_API_KEY") if has(v)), None)
    return (
        ProviderStatus(
            "ollama",
            ollama_tags is not None,
            f"{len(ollama_tags or ())} local models",
            "install from https://ollama.com, then: ollama serve",
        ),
        ProviderStatus(
            "openai",
            bool(openai_bits),
            openai_bits or "API key",
            "export OPENAI_API_KEY=sk-...   (or: smartpipe auth login)",
        ),
        ProviderStatus(
            "gemini",
            gemini_var is not None,
            f"{gemini_var or 'API key'}",
            "export GEMINI_API_KEY=...   (aistudio.google.com)",
        ),
        ProviderStatus(
            "anthropic",
            has("ANTHROPIC_API_KEY"),
            "API key",
            "export ANTHROPIC_API_KEY=sk-ant-...",
        ),
        ProviderStatus(
            "mistral",
            has("MISTRAL_API_KEY"),
            "API key",
            "export MISTRAL_API_KEY=...   (console.mistral.ai)",
        ),
        ProviderStatus(
            "openrouter",
            has("OPENROUTER_API_KEY"),
            "API key",
            "export OPENROUTER_API_KEY=sk-or-...   (openrouter.ai/keys)",
        ),
    )


def has_jina_key(env: Mapping[str, str]) -> bool:
    return bool(env.get("JINA_API_KEY", "").strip())


# --- the three-stage flow's provider menus (TEXT → EMBED → OCR) --------------------------


@dataclass(frozen=True, slots=True)
class StageEntry:
    """One provider row in a stage menu: openai splits into its two wires."""

    provider: str  # what catalogs/refs use ("openai", "ollama", …)
    wire: str  # "api" | "oauth" | "local" - which door connects it
    label: str  # "openai (API key)"
    connected: bool
    badge: str  # "✓ key" / "✓ ChatGPT" / "✓ local" / "needs key"


def _has_key(env: Mapping[str, str], provider: str) -> bool:
    from smartpipe.config.credentials import KEY_ENVS

    return any(env.get(var, "").strip() for var in KEY_ENVS.get(provider, ()))


def key_stage_entry(env: Mapping[str, str], provider: str, label: str | None = None) -> StageEntry:
    connected = _has_key(env, provider)
    return StageEntry(
        provider=provider,
        wire="api",
        label=label or provider,
        connected=connected,
        badge="✓ key" if connected else "needs key",
    )


def text_stage_entries(
    env: Mapping[str, str], *, ollama_up: bool, login: bool
) -> tuple[StageEntry, ...]:
    """Every chat-capable wire. The ChatGPT wire is its own row - different
    transport, different capabilities (no embeddings, so it vanishes in EMBED)."""
    return (
        StageEntry(
            "ollama",
            "local",
            "ollama",
            ollama_up,
            "✓ local" if ollama_up else "needs install (ollama.com)",
        ),
        key_stage_entry(env, "openai", "openai (API key)"),
        StageEntry(
            "openai",
            "oauth",
            "openai (ChatGPT login)",
            login,
            "✓ ChatGPT" if login else "needs login",
        ),
        key_stage_entry(env, "gemini"),
        key_stage_entry(env, "anthropic"),
        key_stage_entry(env, "mistral"),
        key_stage_entry(env, "openrouter"),
    )


def embed_stage_entries(
    env: Mapping[str, str], *, ollama_up: bool, local_available: bool
) -> tuple[StageEntry, ...]:
    """Embedding-capable wires only: no ChatGPT login (that wire has no
    embeddings), no anthropic/openrouter (no embedding endpoints); jina
    appears here and nowhere else."""
    rows: list[StageEntry] = []
    if local_available:
        rows.append(StageEntry("local", "local", "local (built-in, on-device)", True, "✓ local"))
    rows.append(
        StageEntry(
            "ollama",
            "local",
            "ollama",
            ollama_up,
            "✓ local" if ollama_up else "needs install (ollama.com)",
        )
    )
    rows.extend(
        (
            key_stage_entry(env, "openai", "openai (API key)"),
            key_stage_entry(env, "gemini"),
            key_stage_entry(env, "mistral"),
            key_stage_entry(env, "jina"),
        )
    )
    return tuple(rows)


def stage_labels(entries: tuple[StageEntry, ...]) -> tuple[str, ...]:
    width = max(len(entry.label) for entry in entries) + 2
    return tuple(f"{entry.label:<{width}}{entry.badge}" for entry in entries)


def ocr_stage_rows(current: str | None, chat_model: str | None) -> tuple[tuple[str, str], ...]:
    """The curated OCR menu as (action, label) rows - the first row is always
    the one-keypress skip/keep, so Enter never changes anything."""
    rows: list[tuple[str, str]] = [
        ("keep", "skip - documents parse with the built-in local extraction")
        if current is None
        else ("keep", f"keep current: {current}"),
        ("mistral", "mistral/mistral-ocr-latest - the dedicated OCR wire"),
    ]
    if chat_model is not None:
        rows.append(("vision", f"{chat_model} - vision chat, extract-the-text framing"))
    rows.append(("typed", "type a model name instead…"))
    if current is not None:
        rows.append(("unset", "unset - back to the built-in local extraction"))
    return tuple(rows)


# --- ollama tag decisions (the shipped wizard's rules, kept verbatim) ------------------

# Vision-capable families first; ':cloud' passthrough tags compete as equals —
# they are affordable frontier models, and penalizing them while suggesting
# openai/ would be incoherent (owner ruling).
_PREFERRED_FAMILIES = ("llava", "gemma", "qwen", "llama", "mistral", "phi", "kimi", "glm")
_OLLAMA_DEFAULT_EMBED = "embeddinggemma"  # the 'local' profile's pivot anchor


def ollama_chat_tags(names: tuple[str, ...]) -> tuple[str, ...]:
    """Installed tags minus embedding models — never offer an embedder as chat."""
    return tuple(name for name in names if "embed" not in name.lower())


def preferred_index(tags: tuple[str, ...]) -> int:
    """Where the menu cursor starts: the first known family, vision first."""
    lowered = [tag.lower() for tag in tags]
    for family in _PREFERRED_FAMILIES:
        for position, name in enumerate(lowered):
            if family in name:
                return position
    return 0


def first_embed_tag(names: tuple[str, ...]) -> str:
    return next((name for name in names if "embed" in name.lower()), _OLLAMA_DEFAULT_EMBED)


# --- catalog parsers (parsed JSON in, provider-local names out) -------------------------

_OPENAI_CHAT = re.compile(r"^(gpt-|chatgpt-|o\d)")
_OPENAI_NOISE = (
    "embed",
    "whisper",
    "tts",
    "dall-e",
    "moderation",
    "realtime",
    "audio",
    "transcribe",
    "search",
    "image",
    "instruct",
)


_DATED_SNAPSHOT = re.compile(r"-\d{4}-\d{2}-\d{2}$")


def parse_openai_catalog(payload: object) -> tuple[str, ...]:
    """Chat completions models only — the /v1/models list mixes in embeddings,
    audio, image, and moderation endpoints that would poison the menu. The
    live payload lists OLDEST first and repeats every alias as a dated
    snapshot, so unsorted the 30-cap menu shows gpt-3.5 relics and hides the
    flagships: sort newest-created first, drop `-YYYY-MM-DD` snapshots."""
    record = as_record(payload)
    entries = as_items(record.get("data")) if record is not None else None
    rows: list[tuple[int, str]] = [
        (_created_stamp(entry), name)
        for item in entries or ()
        if (entry := as_record(item)) is not None
        and (name := as_str(entry.get("id"))) is not None
        and _OPENAI_CHAT.match(name)
        and not any(noise in name for noise in _OPENAI_NOISE)
        and not _DATED_SNAPSHOT.search(name)
    ]
    rows.sort(key=lambda pair: pair[0], reverse=True)  # stable: ties keep arrival order
    return _deduped(name for _, name in rows)


def _created_stamp(entry: Mapping[str, object]) -> int:
    created = entry.get("created")
    return created if isinstance(created, int) else 0


_GEMINI_NOISE = ("embedding", "tts", "image-generation")  # generateContent-capable, not chat


def parse_gemini_catalog(payload: object) -> tuple[str, ...]:
    """`generateContent`-capable models, names stripped of the ``models/`` prefix."""
    record = as_record(payload)
    entries = as_items(record.get("models")) if record is not None else None
    names: list[str] = []
    for item in entries or ():
        entry = as_record(item)
        if entry is None:
            continue
        name = as_str(entry.get("name"))
        methods = as_items(entry.get("supportedGenerationMethods")) or ()
        if name is None or "generateContent" not in methods:
            continue
        bare = name.removeprefix("models/")
        if not any(noise in bare for noise in _GEMINI_NOISE):
            names.append(bare)
    return _deduped(names)


def parse_anthropic_catalog(payload: object) -> tuple[str, ...]:
    """Every id — Anthropic's list is chat models only."""
    return _data_ids(payload)


def parse_mistral_catalog(payload: object) -> tuple[str, ...]:
    """Models whose capabilities say ``completion_chat`` — the API's own verdict."""
    record = as_record(payload)
    entries = as_items(record.get("data")) if record is not None else None
    names: list[str] = []
    for item in entries or ():
        entry = as_record(item)
        if entry is None:
            continue
        name = as_str(entry.get("id"))
        capabilities = as_record(entry.get("capabilities"))
        if name is None or capabilities is None:
            continue
        if capabilities.get("completion_chat") is True:
            names.append(name)
    return _deduped(names)


def parse_openrouter_catalog(payload: object) -> tuple[str, ...]:
    """Vision-capable models only (owner dial): an OpenRouter menu of 300+
    text-only reroutes buries the multimodal pitch."""
    record = as_record(payload)
    entries = as_items(record.get("data")) if record is not None else None
    names: list[str] = []
    for item in entries or ():
        entry = as_record(item)
        if entry is None:
            continue
        name = as_str(entry.get("id"))
        architecture = as_record(entry.get("architecture"))
        inputs = as_items(architecture.get("input_modalities")) if architecture else None
        if name is not None and inputs is not None and "image" in inputs:
            names.append(name)
    return _deduped(names)


# --- embedding catalogs (the EMBED stage's per-provider lists) ---------------------------

JINA_EMBED_MODELS = ("jina-clip-v2", "jina-embeddings-v3")  # curated - no catalog endpoint
LOCAL_EMBED_MODELS = ("nomic-embed-text-v1.5",)  # the on-device fastembed wire (D44)


def ollama_embed_tags(names: tuple[str, ...]) -> tuple[str, ...]:
    """Installed tags that embed - the complement of ``ollama_chat_tags``."""
    return tuple(name for name in names if "embed" in name.lower())


def parse_openai_embed_catalog(payload: object) -> tuple[str, ...]:
    """The /v1/models ids that embed (text-embedding-*) - chat noise dropped."""
    return tuple(name for name in _data_ids(payload) if "embedding" in name)


def parse_gemini_embed_catalog(payload: object) -> tuple[str, ...]:
    """``embedContent``-capable models - the API's own verdict, like chat."""
    record = as_record(payload)
    entries = as_items(record.get("models")) if record is not None else None
    names: list[str] = []
    for item in entries or ():
        entry = as_record(item)
        if entry is None:
            continue
        name = as_str(entry.get("name"))
        methods = as_items(entry.get("supportedGenerationMethods")) or ()
        if name is not None and "embedContent" in methods:
            names.append(name.removeprefix("models/"))
    return _deduped(names)


def parse_mistral_embed_catalog(payload: object) -> tuple[str, ...]:
    """Mistral's list marks no embed capability - the name is the signal."""
    return tuple(name for name in _data_ids(payload) if "embed" in name.lower())


def _data_ids(payload: object) -> tuple[str, ...]:
    record = as_record(payload)
    entries = as_items(record.get("data")) if record is not None else None
    names = (
        name
        for item in entries or ()
        if (entry := as_record(item)) is not None and (name := as_str(entry.get("id"))) is not None
    )
    return _deduped(names)


def _deduped(names: Iterable[str]) -> tuple[str, ...]:
    return tuple(dict.fromkeys(names))


def capped_catalog(names: tuple[str, ...]) -> tuple[tuple[str, ...], int]:
    """(shown, hidden count) — long catalogs cap at MENU_CAP; typing covers the rest."""
    return names[:MENU_CAP], max(0, len(names) - MENU_CAP)


# --- the embed-pairing table (owner-ratified) --------------------------------------------

_STATIC_PAIRS: Mapping[str, str] = {
    "openai": "openai/text-embedding-3-small",
    "gemini": "gemini/gemini-embedding-001",
    "mistral": "mistral/mistral-embed",
}


def paired_embed(provider: str, ollama_tags: tuple[str, ...] | None) -> str | None:
    """The embedder that coheres with a provider pick; None = leave the ladder alone
    (anthropic has no embeddings wire; openrouter has no ratified pairing)."""
    if provider == "ollama":
        return f"ollama/{first_embed_tag(ollama_tags or ())}"
    return _STATIC_PAIRS.get(provider)


def embed_pair_allowed(current_embed: str | None) -> bool:
    """Fill only when unset, or when the current value belongs to the pairing
    family (it was auto-paired before) — a deliberate user choice is never
    overwritten."""
    if current_embed is None:
        return True
    from smartpipe.core.errors import UsageFault
    from smartpipe.models.base import parse_model_ref

    try:
        ref = parse_model_ref(current_embed)
    except UsageFault:
        return True  # an unparseable value can only be a leftover typo — repair it
    canonical = str(ref)
    if canonical in _STATIC_PAIRS.values():
        return True
    return ref.provider == "ollama" and "embed" in ref.name.lower()


# --- capability chips ---------------------------------------------------------------------


def chip_text(chip: ProbeChip, now: float) -> str:
    """'sees, hears — probed 3d ago' — only what a real probe observed, dated."""
    parts = [word for word, able in (("sees", chip.sees), ("hears", chip.hears)) if able]
    ability = ", ".join(parts) if parts else "text only"
    days = int(max(0.0, now - chip.ts) // 86_400)
    age = "today" if days == 0 else f"{days}d ago"
    return f"{ability} — probed {age}"


def model_labels(
    provider: str,
    names: tuple[str, ...],
    chips: Mapping[str, ProbeChip],
    now: float,
) -> tuple[str, ...]:
    """Menu labels: canonical refs, chip-annotated where a probe has spoken."""
    labels: list[str] = []
    for name in names:
        ref = f"{provider}/{name}"
        chip = chips.get(ref)
        labels.append(ref if chip is None else f"{ref}  ({chip_text(chip, now)})")
    return tuple(labels)


# --- cache staleness ------------------------------------------------------------------------


def cache_day(now: float) -> str:
    """The catalog cache's date stamp — a UTC day; a dated filename IS the TTL."""
    return datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")
