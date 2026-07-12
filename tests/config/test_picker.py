"""The provider-first picker's pure decisions (detection, catalogs, pairing, chips)."""

from __future__ import annotations

import pytest

from smartpipe.config.picker import (
    JINA_EMBED_MODELS,
    LOCAL_EMBED_MODELS,
    MENU_CAP,
    ChipSources,
    ProbeChip,
    RegistryCaps,
    cache_day,
    capped_catalog,
    chip_label,
    chips_for,
    embed_pair_allowed,
    embed_stage_entries,
    first_embed_tag,
    model_labels,
    ocr_stage_rows,
    ollama_chat_tags,
    ollama_embed_tags,
    paired_embed,
    parse_anthropic_catalog,
    parse_gemini_catalog,
    parse_gemini_embed_catalog,
    parse_mistral_catalog,
    parse_mistral_embed_catalog,
    parse_models_dev,
    parse_openai_catalog,
    parse_openai_embed_catalog,
    parse_openrouter_catalog,
    preferred_index,
    stage_labels,
    text_stage_entries,
)

# --- ollama tag helpers ----------------------------------------------------------


def test_ollama_chat_tags_drop_embedders() -> None:
    assert ollama_chat_tags(("nomic-embed-text", "llava", "qwen3:8b")) == ("llava", "qwen3:8b")


def test_preferred_index_prefers_vision_families_first() -> None:
    tags = ("random-model", "qwen3:8b", "llava")
    assert preferred_index(tags) == tags.index("llava")  # llava outranks qwen


def test_preferred_index_falls_back_to_first() -> None:
    assert preferred_index(("mystery-model",)) == 0
    assert preferred_index(()) == 0


def test_first_embed_tag_detected_or_default() -> None:
    assert first_embed_tag(("llava", "mxbai-embed-large")) == "mxbai-embed-large"
    assert first_embed_tag(("llava",)) == "embeddinggemma"


# --- catalog parsers -------------------------------------------------------------


def test_openai_catalog_keeps_chat_models_only() -> None:
    payload = {
        "data": [
            {"id": "gpt-5.4-mini"},
            {"id": "o4-mini"},
            {"id": "chatgpt-4o-latest"},
            {"id": "text-embedding-3-small"},
            {"id": "whisper-1"},
            {"id": "gpt-4o-realtime-preview"},
            {"id": "gpt-4o-audio-preview"},
            {"id": "tts-1"},
            {"id": "dall-e-3"},
            {"id": "gpt-image-1"},
            {"id": "gpt-4o-transcribe"},
            {"id": "gpt-4o-search-preview"},
            {"id": "omni-moderation-latest"},
            {"id": "gpt-3.5-turbo-instruct"},
            {"id": "davinci-002"},
        ]
    }
    assert parse_openai_catalog(payload) == ("gpt-5.4-mini", "o4-mini", "chatgpt-4o-latest")


def test_openai_catalog_newest_first_and_no_dated_snapshots() -> None:
    """The live /v1/models payload lists oldest-first and includes dated
    snapshots - unsorted, the 30-cap menu shows gpt-3.5 relics and hides
    the current flagships. Sort by created (newest first), drop snapshots."""
    payload = {
        "data": [
            {"id": "gpt-3.5-turbo", "created": 1_100},
            {"id": "gpt-5.4", "created": 1_400},
            {"id": "gpt-5.4-2026-03-05", "created": 1_401},
            {"id": "gpt-5.5", "created": 1_500},
            {"id": "o4-mini", "created": 1_300},
        ]
    }
    assert parse_openai_catalog(payload) == ("gpt-5.5", "gpt-5.4", "o4-mini", "gpt-3.5-turbo")


def test_openai_catalog_tolerates_junk_shapes() -> None:
    assert parse_openai_catalog({"data": "nope"}) == ()
    assert parse_openai_catalog([1, 2]) == ()
    assert parse_openai_catalog({"data": [{"id": 3}, "x", {"id": "gpt-5.4"}]}) == ("gpt-5.4",)


def test_openai_catalog_dedupes_preserving_order() -> None:
    payload = {"data": [{"id": "gpt-5.4"}, {"id": "gpt-5.4"}]}
    assert parse_openai_catalog(payload) == ("gpt-5.4",)


def test_gemini_catalog_strips_prefix_and_filters_generate_content() -> None:
    payload = {
        "models": [
            {"name": "models/gemini-2.5-flash", "supportedGenerationMethods": ["generateContent"]},
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/imagen-4", "supportedGenerationMethods": ["predict"]},
            {
                "name": "models/text-embedding-004",
                "supportedGenerationMethods": ["generateContent", "embedContent"],
            },
            # live-caught: TTS and image-generation variants also claim
            # generateContent — they'd poison a CHAT menu
            {
                "name": "models/gemini-2.5-flash-preview-tts",
                "supportedGenerationMethods": ["generateContent"],
            },
            {
                "name": "models/gemini-2.0-flash-preview-image-generation",
                "supportedGenerationMethods": ["generateContent"],
            },
        ]
    }
    assert parse_gemini_catalog(payload) == ("gemini-2.5-flash",)


def test_anthropic_catalog_lists_every_id() -> None:
    payload = {"data": [{"id": "claude-sonnet-5"}, {"id": "claude-opus-4-8"}]}
    assert parse_anthropic_catalog(payload) == ("claude-sonnet-5", "claude-opus-4-8")


def test_mistral_catalog_requires_completion_chat() -> None:
    payload: dict[str, object] = {
        "data": [
            {"id": "mistral-medium-2508", "capabilities": {"completion_chat": True}},
            {"id": "mistral-embed", "capabilities": {"completion_chat": False}},
            {"id": "mystery", "capabilities": {}},
            {"id": "no-caps"},
        ]
    }
    assert parse_mistral_catalog(payload) == ("mistral-medium-2508",)


def test_openrouter_catalog_keeps_vision_capable_models_only() -> None:
    payload = {
        "data": [
            {"id": "x-ai/grok-4.5", "architecture": {"input_modalities": ["text", "image"]}},
            {"id": "some/text-only", "architecture": {"input_modalities": ["text"]}},
            {"id": "no-arch"},
        ]
    }
    assert parse_openrouter_catalog(payload) == ("x-ai/grok-4.5",)


# --- the pairing table (owner-ratified) --------------------------------------------


@pytest.mark.parametrize(
    ("provider", "expected"),
    [
        ("openai", "openai/text-embedding-3-small"),
        ("gemini", "gemini/gemini-embedding-001"),
        ("mistral", "mistral/mistral-embed"),
        ("anthropic", None),  # no embeddings wire — the default ladder applies
        ("openrouter", None),
    ],
)
def test_paired_embed_static_table(provider: str, expected: str | None) -> None:
    assert paired_embed(provider, None) == expected


def test_paired_embed_ollama_prefers_a_detected_tag() -> None:
    assert paired_embed("ollama", ("llava", "nomic-embed-text")) == "ollama/nomic-embed-text"


def test_paired_embed_ollama_falls_back_to_the_shipped_default() -> None:
    assert paired_embed("ollama", ("llava",)) == "ollama/embeddinggemma"
    assert paired_embed("ollama", None) == "ollama/embeddinggemma"


def test_pairing_fills_only_unset_or_previously_paired() -> None:
    assert embed_pair_allowed(None)  # unset: fill
    assert embed_pair_allowed("openai/text-embedding-3-small")  # ours: repairable
    assert embed_pair_allowed("gemini/gemini-embedding-001")
    assert embed_pair_allowed("mistral/mistral-embed")
    assert embed_pair_allowed("ollama/nomic-embed-text")  # ollama embed tag: ours
    assert embed_pair_allowed("ollama/embeddinggemma")
    # deliberate user choices are never clobbered
    assert not embed_pair_allowed("jina/jina-clip-v2")
    assert not embed_pair_allowed("local/nomic-embed-text-v1.5")
    assert not embed_pair_allowed("openai/text-embedding-3-large")


def test_pairing_treats_an_unparseable_value_as_replaceable() -> None:
    assert embed_pair_allowed("   ")


# --- capability chips ---------------------------------------------------------------


def test_chips_precedence_probed_beats_registry_beats_declared() -> None:
    now = 1_000_000.0
    sources = ChipSources(
        probed={"openai/gpt-5.4-mini": ProbeChip(sees=True, hears=False, ts=now - 3 * 86_400)},
        registry={
            "openai/gpt-5.4-mini": RegistryCaps(image=True, audio=True),
            "openai/o4-mini": RegistryCaps(image=True, audio=False),
        },
        declared={
            "openai/gpt-5.4-mini": ("image", "audio"),
            "openai/o4-mini": ("audio",),
            "ollama/selfhosted": ("image",),
        },
    )
    probed = chips_for("openai/gpt-5.4-mini", sources, now)
    assert probed is not None and probed.source == "probed"
    assert probed.image and not probed.audio  # the probe's verdict, not the registry's
    registry = chips_for("openai/o4-mini", sources, now)
    assert registry is not None and registry.source == "registry"
    declared = chips_for("ollama/selfhosted", sources, now)
    assert declared is not None and declared.source == "declared"
    assert chips_for("ollama/unknown", sources, now) is None  # no source = no claims


def test_chip_label_formats_by_source() -> None:
    now = 1_000_000.0
    probed = chips_for(
        "m",
        ChipSources(
            probed={"m": ProbeChip(sees=True, hears=False, ts=now - 3 * 86_400)},
            registry={},
            declared={},
        ),
        now,
    )
    assert probed is not None
    assert chip_label(probed) == "text · image - probed 3d ago"
    fresh = chips_for(
        "m",
        ChipSources(
            probed={"m": ProbeChip(sees=False, hears=False, ts=now)}, registry={}, declared={}
        ),
        now,
    )
    assert fresh is not None
    assert chip_label(fresh) == "text - probed today"
    registry = chips_for(
        "m", ChipSources(probed={}, registry={"m": RegistryCaps(True, True)}, declared={}), now
    )
    assert registry is not None
    assert chip_label(registry) == "text · image · audio"  # the ambient truth, unlabeled
    declared = chips_for("m", ChipSources(probed={}, registry={}, declared={"m": ("image",)}), now)
    assert declared is not None
    assert chip_label(declared) == "text · image - declared"


def test_parse_models_dev_maps_providers_and_input_modalities() -> None:
    payload: dict[str, object] = {
        "openai": {
            "models": {
                "gpt-5.4-mini": {"modalities": {"input": ["text", "image"], "output": ["text"]}},
                "o4-mini": {"modalities": {"input": ["text"]}},
            }
        },
        "google": {
            "models": {"gemini-3.1-flash": {"modalities": {"input": ["text", "image", "audio"]}}}
        },
        "unknown-provider": {"models": {"x": {"modalities": {"input": ["text"]}}}},
        "anthropic": {"models": {"broken": {}}},
    }
    caps = parse_models_dev(payload)
    assert caps["openai/gpt-5.4-mini"] == RegistryCaps(image=True, audio=False)
    assert caps["openai/o4-mini"] == RegistryCaps(image=False, audio=False)
    assert caps["gemini/gemini-3.1-flash"] == RegistryCaps(
        image=True, audio=True
    )  # google → gemini
    assert not any(ref.startswith("unknown-provider") for ref in caps)
    assert "anthropic/broken" not in caps  # malformed entries claim nothing


def test_parse_models_dev_junk_is_empty() -> None:
    assert parse_models_dev(None) == {}
    assert parse_models_dev([1, 2]) == {}


def test_model_labels_annotate_from_any_source() -> None:
    now = 1_000_000.0
    sources = ChipSources(
        probed={"ollama/llava": ProbeChip(sees=True, hears=False, ts=now)},
        registry={},
        declared={"ollama/qwen3:8b": ("image",)},
    )
    labels = model_labels("ollama", ("llava", "qwen3:8b", "phi4"), sources, now)
    assert labels == (
        "ollama/llava  (text · image - probed today)",
        "ollama/qwen3:8b  (text · image - declared)",
        "ollama/phi4",
    )
    assert model_labels("ollama", ("qwen3.5:cloud",), sources, now) == (
        "ollama/qwen3.5:cloud  (cloud · schema unverified)",
    )


# --- cache staleness ------------------------------------------------------------------


def test_cache_day_is_a_utc_date() -> None:
    assert cache_day(0.0) == "1970-01-01"


def test_capped_catalog_caps_long_menus() -> None:
    names = tuple(f"model-{i}" for i in range(MENU_CAP + 7))
    shown, hidden = capped_catalog(names)
    assert len(shown) == MENU_CAP
    assert hidden == 7
    assert capped_catalog(("a", "b")) == (("a", "b"), 0)


# --- the three-stage flow's provider menus ---------------------------------------------


def test_text_stage_lists_openai_twice_with_badges() -> None:
    entries = text_stage_entries({"OPENAI_API_KEY": "sk-x"}, ollama_up=True, login=False)
    labels = [entry.label for entry in entries]
    assert labels == [
        "ollama",
        "openai (API key)",
        "openai (ChatGPT login)",
        "gemini",
        "anthropic",
        "mistral",
        "openrouter",
    ]
    by_label = {entry.label: entry for entry in entries}
    assert by_label["openai (API key)"].badge == "✓ key"
    assert by_label["openai (ChatGPT login)"].badge == "needs login"
    assert by_label["ollama"].badge == "✓ local"
    assert by_label["mistral"].badge == "needs key"
    assert not by_label["mistral"].connected


def test_text_stage_chatgpt_badge_when_logged_in() -> None:
    entries = text_stage_entries({}, ollama_up=False, login=True)
    by_label = {entry.label: entry for entry in entries}
    assert by_label["openai (ChatGPT login)"].badge == "✓ ChatGPT"
    assert "ollama.com" in by_label["ollama"].badge  # the fix rides the badge


def test_embed_stage_membership_follows_capability() -> None:
    entries = embed_stage_entries({"JINA_API_KEY": "j"}, ollama_up=True, local_available=True)
    labels = [entry.label for entry in entries]
    assert labels == [
        "local (built-in, on-device)",
        "ollama",
        "openai (API key)",
        "gemini",
        "mistral",
        "jina",
    ]
    # no ChatGPT wire (no embeddings), no anthropic, no openrouter; jina only here
    assert all("ChatGPT" not in label for label in labels)
    by_label = {entry.label: entry for entry in entries}
    assert by_label["jina"].badge == "✓ key"
    assert by_label["local (built-in, on-device)"].connected


def test_embed_stage_without_fastembed_drops_the_local_row() -> None:
    entries = embed_stage_entries({}, ollama_up=False, local_available=False)
    assert all("built-in" not in entry.label for entry in entries)


def test_stage_labels_align_badges() -> None:
    entries = text_stage_entries({}, ollama_up=False, login=False)
    labels = stage_labels(entries)
    assert labels[1].startswith("openai (API key)")
    assert labels[1].endswith("needs key")


# --- embed catalogs ----------------------------------------------------------------------


def test_parse_openai_embed_catalog_keeps_embedders_only() -> None:
    payload = {
        "data": [
            {"id": "text-embedding-3-small"},
            {"id": "gpt-5.4-mini"},
            {"id": "text-embedding-3-large"},
            {"id": "whisper-1"},
        ]
    }
    assert parse_openai_embed_catalog(payload) == (
        "text-embedding-3-small",
        "text-embedding-3-large",
    )


def test_parse_gemini_embed_catalog_requires_embed_content() -> None:
    payload = {
        "models": [
            {"name": "models/gemini-embedding-001", "supportedGenerationMethods": ["embedContent"]},
            {"name": "models/gemini-3.1-flash", "supportedGenerationMethods": ["generateContent"]},
        ]
    }
    assert parse_gemini_embed_catalog(payload) == ("gemini-embedding-001",)


def test_parse_mistral_embed_catalog_by_name() -> None:
    payload = {"data": [{"id": "mistral-embed"}, {"id": "mistral-small-latest"}]}
    assert parse_mistral_embed_catalog(payload) == ("mistral-embed",)


def test_ollama_embed_tags_is_the_chat_complement() -> None:
    names = ("llava", "nomic-embed-text", "qwen3:8b")
    assert ollama_embed_tags(names) == ("nomic-embed-text",)
    assert set(ollama_embed_tags(names)) | set(ollama_chat_tags(names)) == set(names)


def test_curated_embed_lists_exist() -> None:
    assert "jina-clip-v2" in JINA_EMBED_MODELS
    assert "nomic-embed-text-v1.5" in LOCAL_EMBED_MODELS


# --- the OCR stage's curated rows --------------------------------------------------------


def test_ocr_rows_unset_lead_with_the_skip() -> None:
    rows = ocr_stage_rows(None, "openai/gpt-5.4-mini")
    assert rows[0][0] == "keep"
    assert rows[0][1].startswith("skip - ")
    actions = [action for action, _label in rows]
    assert actions == ["keep", "sep", "mistral", "vision", "sep", "typed"]
    assert any("extract-the-text" in label for _a, label in rows)


def test_ocr_rows_set_offer_keep_and_unset() -> None:
    rows = ocr_stage_rows("mistral/mistral-ocr-latest", None)
    assert rows[0] == ("keep", "keep current: mistral/mistral-ocr-latest")
    actions = [action for action, _label in rows]
    assert actions == ["keep", "sep", "mistral", "sep", "typed", "unset"]


def test_vision_candidates_follow_chip_precedence_and_dedupe() -> None:
    from smartpipe.config.picker import vision_ocr_candidates

    chips = ChipSources(
        probed={
            "ollama/llava": ProbeChip(sees=True, hears=False, ts=0.0),
            "ollama/qwen3:8b": ProbeChip(sees=False, hears=False, ts=0.0),  # blind: dropped
        },
        registry={
            "openai/gpt-5.4-mini": RegistryCaps(image=True, audio=False),
            "ollama/llava": RegistryCaps(image=True, audio=False),  # dupe: probed wins
            "openai/o4-mini": RegistryCaps(image=False, audio=False),  # text-only: dropped
        },
        declared={"gemini/gemini-3.1-flash-lite": ("text", "image")},
    )
    assert vision_ocr_candidates(chips) == (
        "ollama/llava",
        "gemini/gemini-3.1-flash-lite",
        "openai/gpt-5.4-mini",
    )


def test_vision_candidates_drop_openai_capability_dump_noise() -> None:
    from smartpipe.config.picker import vision_ocr_candidates

    # models.dev flags these openai variants image-input, but they are NOT
    # document parsers — the same noise the text/chat stage already denylists.
    chips = ChipSources(
        probed={},
        registry={
            "openai/chatgpt-image-latest": RegistryCaps(image=True, audio=False),
            "openai/gpt-realtime-2.1": RegistryCaps(image=True, audio=False),
            "openai/gpt-4o-search-preview": RegistryCaps(image=True, audio=False),
            "openai/gpt-5.4": RegistryCaps(image=True, audio=False),
        },
        declared={},
    )
    assert vision_ocr_candidates(chips) == ("openai/gpt-5.4",)


def test_vision_candidates_drop_dated_snapshots() -> None:
    from smartpipe.config.picker import vision_ocr_candidates

    chips = ChipSources(
        probed={},
        registry={
            "openai/gpt-4o-2024-08-06": RegistryCaps(image=True, audio=False),  # dated pin
            "openai/gpt-4o": RegistryCaps(image=True, audio=False),
        },
        declared={},
    )
    assert vision_ocr_candidates(chips) == ("openai/gpt-4o",)


def test_vision_candidates_lead_with_one_model_per_provider() -> None:
    from smartpipe.config.picker import vision_ocr_candidates

    # a long openai list must not bury the other providers under the menu cap:
    # the survivors lead with one model per provider (first-seen), then the rest.
    chips = ChipSources(
        probed={},
        registry={
            "openai/gpt-4o": RegistryCaps(image=True, audio=False),
            "openai/gpt-4.1": RegistryCaps(image=True, audio=False),
            "openai/gpt-5.4": RegistryCaps(image=True, audio=False),
            "anthropic/claude-opus-4-8": RegistryCaps(image=True, audio=False),
            "gemini/gemini-3.1-flash": RegistryCaps(image=True, audio=False),
        },
        declared={},
    )
    assert vision_ocr_candidates(chips) == (
        "openai/gpt-4o",  # one per provider first…
        "anthropic/claude-opus-4-8",
        "gemini/gemini-3.1-flash",
        "openai/gpt-4.1",  # …then each provider's tail, in order
        "openai/gpt-5.4",
    )


def test_ocr_rows_list_vision_candidates_between_vision_chat_and_typed() -> None:
    rows = ocr_stage_rows(
        None,
        "openai/gpt-5.4-mini",
        ("ollama/llava", "openai/gpt-5.4-mini", "mistral/mistral-ocr-latest"),
    )
    actions = [action for action, _label in rows]
    # the picked chat model and the dedicated wire never repeat as pick rows
    assert actions == ["keep", "sep", "mistral", "vision", "pick", "sep", "typed"]
    assert rows[4] == ("pick", "ollama/llava")


def test_ocr_rows_cap_at_the_menu_height_and_count_the_hidden() -> None:
    vision = tuple(f"openrouter/vendor/model-{index:02}" for index in range(40))
    rows = ocr_stage_rows("ollama/llava", "openai/gpt-5.4-mini", vision)
    assert len(rows) <= MENU_CAP
    picks = [label for action, label in rows if action == "pick"]
    assert picks == list(vision[: len(picks)])  # order kept, capped
    hidden = 40 - len(picks)
    typed = next(label for action, label in rows if action == "typed")
    assert typed == f"type a model name instead… ({hidden} more not shown)"
    assert rows[-1][0] == "unset"  # the escape rows survive the cap


# --- the STT stage's rows ----------------------------------------------------------------


def test_stt_rows_unset_lead_with_the_auto_skip() -> None:
    from smartpipe.config.picker import stt_stage_rows

    rows = stt_stage_rows(None)
    assert rows[0][0] == "keep"  # Enter never changes anything (the OCR pin)
    assert rows[0][1].startswith("skip - auto")
    actions = [action for action, _label in rows]
    # owner ruling 2026-07-12: local second, then the openai wires best-first,
    # each ("pick"-style) row's ACTION carrying its full ref through one key door
    assert actions == [
        "keep",
        "sep",
        "local",
        "openai/gpt-4o-transcribe",
        "openai/gpt-4o-mini-transcribe",
        "openai/whisper-1",
        "sep",
        "typed",
    ]
    assert any("on-device" in label for _a, label in rows)


def test_stt_rows_carry_honest_suffixes_and_lead_with_their_ref() -> None:
    from smartpipe.config.picker import stt_stage_rows

    labels = dict(stt_stage_rows(None))
    assert labels["openai/gpt-4o-transcribe"].startswith(
        "openai/gpt-4o-transcribe - best transcription quality"
    )
    assert labels["openai/gpt-4o-mini-transcribe"].startswith(
        "openai/gpt-4o-mini-transcribe - cheaper"
    )
    assert labels["openai/whisper-1"].startswith("openai/whisper-1 - the verbatim classic")


def test_stt_rows_set_offer_keep_and_unset() -> None:
    from smartpipe.config.picker import stt_stage_rows

    rows = stt_stage_rows("local")
    assert rows[0] == ("keep", "keep current: local")
    actions = [action for action, _label in rows]
    assert actions == [
        "keep",
        "sep",
        "local",
        "openai/gpt-4o-transcribe",
        "openai/gpt-4o-mini-transcribe",
        "openai/whisper-1",
        "sep",
        "typed",
        "unset",
    ]
    assert rows[-1][1].startswith("unset - back to auto")
