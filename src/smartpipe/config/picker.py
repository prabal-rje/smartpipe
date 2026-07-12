"""Pure decisions behind the provider-first picker (the ``smartpipe use`` flow).

Everything here is a value-in/value-out function: stage menus, catalog
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
    "STT_STAGE_MODELS",
    "CapChips",
    "ChipSources",
    "ProbeChip",
    "RegistryCaps",
    "StageEntry",
    "cache_day",
    "capped_catalog",
    "chip_label",
    "chips_for",
    "embed_pair_allowed",
    "embed_stage_entries",
    "first_embed_tag",
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
    "parse_models_dev",
    "parse_openai_catalog",
    "parse_openai_embed_catalog",
    "parse_openrouter_catalog",
    "preferred_index",
    "stage_labels",
    "stt_stage_rows",
    "text_stage_entries",
    "vision_ocr_candidates",
]

MENU_CAP = 30  # a menu taller than the terminal helps nobody — typed input covers the rest


@dataclass(frozen=True, slots=True)
class ProbeChip:
    """What ``doctor --probe`` PAID to learn about one model (no cache = no claims)."""

    sees: bool
    hears: bool
    ts: float


# --- the staged flow's provider menus (TEXT → EMBED → OCR → STT) -------------------------


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


def vision_ocr_candidates(chips: ChipSources) -> tuple[str, ...]:
    """Every catalog ref a chip source says can SEE (item 73c) — the OCR
    stage's menu fodder, CURATED like the text stage rather than dumped raw.
    Source order is the chip precedence (a paid probe outranks the registry
    outranks a self-claim; refs dedupe on first sight); then the SAME openai
    noise denylist + dated-snapshot drop the chat stage applies weeds out the
    ``chatgpt-image-latest``/``gpt-realtime`` variants models.dev tags
    image-input but that are no document parsers; then the survivors lead with
    one model per provider so a single long provider list can't bury the rest
    under ``MENU_CAP``.

    Deferred second pass (item C1): a hardcoded per-provider flagship shortlist
    and TITLED provider section headers (``io/arrow_menu`` now has blank
    separator rows; headers with text remain deferred). This pass curates and
    orders the DISCOVERED
    candidates only — the dedicated ``mistral-ocr-latest`` wire and the picked
    vision chat model already lead the menu as their own rows in
    ``ocr_stage_rows``. A newest-first sort like the chat stage's needs a
    ``created`` stamp the models.dev registry does not carry, so ordering here
    is provider-diverse rather than dated."""
    probed = (ref for ref, chip in chips.probed.items() if chip.sees)
    declared = (ref for ref, claims in chips.declared.items() if "image" in claims)
    registry = (ref for ref, caps in chips.registry.items() if caps.image)
    seen = _deduped((*probed, *declared, *registry))
    return _provider_diverse(tuple(ref for ref in seen if _ocr_menu_worthy(ref)))


def _ocr_menu_worthy(ref: str) -> bool:
    """A vision ref fit for the OCR pick list: no openai capability-dump noise,
    no dated snapshot — the same curation the text/chat stage applies."""
    provider, _, name = ref.partition("/")
    if provider == "openai" and _is_openai_noise(name):
        return False
    return not _is_dated_snapshot(name)


def _provider_diverse(refs: tuple[str, ...]) -> tuple[str, ...]:
    """Lead with one ref per provider (first-seen order), then each provider's
    tail in original order — provider breadth survives the menu cap instead of
    one provider's long list crowding everyone else out."""
    by_provider: dict[str, list[str]] = {}
    for ref in refs:
        by_provider.setdefault(ref.partition("/")[0], []).append(ref)
    groups = tuple(by_provider.values())
    firsts = tuple(group[0] for group in groups)
    tails = tuple(ref for group in groups for ref in group[1:])
    return firsts + tails


def ocr_stage_rows(
    current: str | None, chat_model: str | None, vision: tuple[str, ...] = ()
) -> tuple[tuple[str, str], ...]:
    """The OCR menu as (action, label) rows - the first row is always the
    one-keypress skip/keep, so Enter never changes anything. ``vision`` (item
    73c): the vision-capable catalog refs, offered as ("pick", ref) rows and
    capped so the whole menu honors MENU_CAP — the typed row covers the rest,
    naming the hidden count like every other stage."""
    rows: list[tuple[str, str]] = [
        ("keep", "skip - documents parse with the built-in local extraction")
        if current is None
        else ("keep", f"keep current: {current}"),
        ("sep", ""),  # paragraph break: keep/skip | the model rows
        ("mistral", "mistral/mistral-ocr-latest - the dedicated OCR wire"),
    ]
    if chat_model is not None:
        rows.append(("vision", f"{chat_model} - vision chat, extract-the-text framing"))
    fixed = len(rows) + 2 + (1 if current is not None else 0)  # + sep + typed [+ unset]
    exclude = {"mistral/mistral-ocr-latest", chat_model, current}
    candidates = [ref for ref in vision if ref not in exclude]
    room = max(0, MENU_CAP - fixed)
    shown, hidden = candidates[:room], max(0, len(candidates) - room)
    rows.extend(("pick", ref) for ref in shown)
    type_it = "type a model name instead…"
    rows.append(("sep", ""))  # paragraph break: model rows | the escape hatches
    rows.append(("typed", type_it if not hidden else f"{type_it} ({hidden} more not shown)"))
    if current is not None:
        rows.append(("unset", "unset - back to the built-in local extraction"))
    return tuple(rows)


_STT_AUTO_HINT = "auto (whisper-1 with an OpenAI key, else local whisper)"

# Owner ruling 2026-07-12: the openai transcription wires, best quality first —
# they all ride the same /v1/audio/transcriptions endpoint and the same key
# door. Each row's ACTION carries its full ref so ONE stage arm handles all of
# them; the label leads with the ref plus a short honest suffix. Completions
# (``suggest_stt_models``) derive their curated order from this tuple.
STT_STAGE_MODELS: tuple[tuple[str, str], ...] = (
    ("openai/gpt-4o-transcribe", "best transcription quality (needs an OpenAI API key)"),
    ("openai/gpt-4o-mini-transcribe", "cheaper, nearly as good"),
    ("openai/whisper-1", "the verbatim classic"),
)


def stt_stage_rows(current: str | None) -> tuple[tuple[str, str], ...]:
    """The STT menu as (action, label) rows - the first row is always the
    one-keypress skip/keep, so Enter never changes anything (the same pin as
    OCR). ``local`` pins on-device whisper; the openai rows (best first) each
    carry their full ref as the action — one arm, one key door."""
    rows: list[tuple[str, str]] = [
        ("keep", f"skip - {_STT_AUTO_HINT}")
        if current is None
        else ("keep", f"keep current: {current}"),
        ("sep", ""),  # paragraph break: keep/skip | the engine rows
        ("local", "local whisper - transcribes on-device (free, private)"),
        *((ref, f"{ref} - {suffix}") for ref, suffix in STT_STAGE_MODELS),
        ("sep", ""),  # paragraph break: engine rows | the escape hatches
        ("typed", "type a model name instead…"),
    ]
    if current is not None:
        rows.append(("unset", f"unset - back to {_STT_AUTO_HINT}"))
    return tuple(rows)


# --- ollama tag decisions (the shipped wizard's rules, kept verbatim) ------------------

# Vision-capable families first; ':cloud' passthrough tags compete as equals —
# they are affordable frontier models, and penalizing them while suggesting
# openai/ would be incoherent (owner ruling).
_PREFERRED_FAMILIES = ("llava", "gemma", "qwen", "llama", "mistral", "phi", "kimi", "glm")
_OLLAMA_DEFAULT_EMBED = "embeddinggemma"  # the shipped local pivot anchor


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


def _is_openai_noise(name: str) -> bool:
    """A capability-dump id (realtime/image/audio/search/embed…) — not a chat
    or document model. Shared by the text stage and the OCR stage's curation."""
    return any(noise in name for noise in _OPENAI_NOISE)


def _is_dated_snapshot(name: str) -> bool:
    """A `-YYYY-MM-DD` pin that repeats an undated alias; the alias is enough."""
    return _DATED_SNAPSHOT.search(name) is not None


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
        and not _is_openai_noise(name)
        and not _is_dated_snapshot(name)
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


# --- capability chips: probed (cache) > registry (models.dev) > declared (config) ---------


@dataclass(frozen=True, slots=True)
class RegistryCaps:
    """What models.dev's public registry says a model takes as INPUT."""

    image: bool
    audio: bool


@dataclass(frozen=True, slots=True)
class CapChips:
    """One row's chips + where the claim comes from. Display only - runtime
    stays attempt-based; a chip never gates a request."""

    image: bool
    audio: bool
    source: str  # "probed" | "registry" | "declared"
    age_days: int | None = None  # probed only


# models.dev provider ids → smartpipe provider names (identity where equal)
_MODELS_DEV_PROVIDERS: Mapping[str, str] = {
    "openai": "openai",
    "anthropic": "anthropic",
    "google": "gemini",
    "mistral": "mistral",
    "openrouter": "openrouter",
}


def parse_models_dev(payload: object) -> dict[str, RegistryCaps]:
    """models.dev's api.json → ref → input modalities, for providers we route."""
    record = as_record(payload)
    caps: dict[str, RegistryCaps] = {}
    for dev_id, provider in _MODELS_DEV_PROVIDERS.items():
        entry = as_record(record.get(dev_id)) if record is not None else None
        models = as_record(entry.get("models")) if entry is not None else None
        for name, value in (models or {}).items():
            model = as_record(value)
            modalities = as_record(model.get("modalities")) if model is not None else None
            inputs = as_items(modalities.get("input")) if modalities is not None else None
            if inputs is None:
                continue
            caps[f"{provider}/{name}"] = RegistryCaps(
                image="image" in inputs, audio="audio" in inputs
            )
    return caps


@dataclass(frozen=True, slots=True)
class ChipSources:
    """The three chip sources a menu consults, ready-loaded by the wiring."""

    probed: Mapping[str, ProbeChip]
    registry: Mapping[str, RegistryCaps]
    declared: Mapping[str, tuple[str, ...]]

    @staticmethod
    def none() -> ChipSources:
        return ChipSources(probed={}, registry={}, declared={})


def chips_for(ref: str, sources: ChipSources, now: float) -> CapChips | None:
    """The precedence: a paid probe outranks the registry outranks a self-claim."""
    chip = sources.probed.get(ref)
    if chip is not None:
        days = int(max(0.0, now - chip.ts) // 86_400)
        return CapChips(image=chip.sees, audio=chip.hears, source="probed", age_days=days)
    from_registry = sources.registry.get(ref)
    if from_registry is not None:
        return CapChips(image=from_registry.image, audio=from_registry.audio, source="registry")
    claims = sources.declared.get(ref)
    if claims is not None:
        return CapChips(image="image" in claims, audio="audio" in claims, source="declared")
    return None


def chip_label(chips: CapChips) -> str:
    """'text · image · audio', suffixed by the claim's provenance where it
    matters: probed chips are dated, declared chips say so, registry chips
    stand bare (the ambient public truth)."""
    parts = [
        "text",
        *(name for name, able in (("image", chips.image), ("audio", chips.audio)) if able),
    ]
    body = " · ".join(parts)
    match chips.source:
        case "probed":
            age = "today" if chips.age_days == 0 else f"{chips.age_days}d ago"
            return f"{body} - probed {age}"
        case "declared":
            return f"{body} - declared"
        case _:
            return body


def model_labels(
    provider: str,
    names: tuple[str, ...],
    sources: ChipSources,
    now: float,
) -> tuple[str, ...]:
    """Menu labels: canonical refs, chip-annotated where any source has spoken."""
    labels: list[str] = []
    for name in names:
        ref = f"{provider}/{name}"
        chips = chips_for(ref, sources, now)
        if provider == "ollama" and name.endswith(":cloud"):
            labels.append(f"{ref}  (cloud · schema unverified)")
        else:
            labels.append(ref if chips is None else f"{ref}  ({chip_label(chips)})")
    return tuple(labels)


# --- cache staleness ------------------------------------------------------------------------


def cache_day(now: float) -> str:
    """The catalog cache's date stamp — a UTC day; a dated filename IS the TTL."""
    return datetime.fromtimestamp(now, tz=UTC).strftime("%Y-%m-%d")
