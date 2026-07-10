from __future__ import annotations

from pathlib import Path
from typing import TYPE_CHECKING

import pytest

from smartpipe.cli.config_cmd import run_config_flow
from smartpipe.config.picker import ProbeChip
from smartpipe.config.store import Config, load_config
from tests.conftest import RunCli

if TYPE_CHECKING:
    from collections.abc import Mapping

_NOW = 1_751_900_000.0  # a fixed clock — chips date against it


@pytest.fixture
def config_home(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    monkeypatch.delenv("SMARTPIPE_MODEL", raising=False)
    monkeypatch.delenv("SMARTPIPE_EMBED_MODEL", raising=False)
    return tmp_path / "smartpipe" / "config.toml"


# --- config model / embed-model -----------------------------------------------


def test_set_model_writes_canonical_and_confirms(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "model", "gpt-5.4-mini"])
    assert code == 0
    assert out.strip() == "model set to openai/gpt-5.4-mini"
    assert load_config(config_home).model == "openai/gpt-5.4-mini"


def test_set_embed_model(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "embed-model", "nomic-embed-text"])
    assert code == 0
    assert out.strip() == "embed-model set to ollama/nomic-embed-text"
    assert load_config(config_home).embed_model == "ollama/nomic-embed-text"


def test_set_model_preserves_other_fields(run_cli: RunCli, config_home: Path) -> None:
    run_cli(["config", "embed-model", "nomic-embed-text"])
    run_cli(["config", "model", "ollama/qwen3:8b"])
    config = load_config(config_home)
    assert config.model == "ollama/qwen3:8b"
    assert config.embed_model == "ollama/nomic-embed-text"


def test_media_previews_off_persists_the_kill_switch(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "media-previews", "off"])
    assert code == 0
    assert "media-previews off" in out
    assert load_config(config_home).media_previews is False


def test_media_previews_on_turns_it_back(run_cli: RunCli, config_home: Path) -> None:
    run_cli(["config", "media-previews", "off"])
    code, out, _err = run_cli(["config", "media-previews", "on"])
    assert code == 0
    assert "media-previews on" in out
    assert load_config(config_home).media_previews is True


def test_set_bad_model_is_a_usage_error(run_cli: RunCli, config_home: Path) -> None:
    code, _out, err = run_cli(["config", "model", "   "])
    assert code == 64
    assert "no model given" in err


# --- config show --------------------------------------------------------------


def test_show_reports_defaults(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "show"])
    assert code == 0
    lines = out.splitlines()
    assert lines[0].startswith("model")
    assert "(auto-detect)" in lines[0]
    assert "(default)" in lines[0]
    assert lines[-1].startswith("config file")


def test_show_reflects_a_saved_model(run_cli: RunCli, config_home: Path) -> None:
    run_cli(["config", "model", "ollama/qwen3:8b"])
    _code, out, _err = run_cli(["config", "show"])
    model_line = out.splitlines()[0]
    assert "ollama/qwen3:8b" in model_line
    assert "(config file)" in model_line


def test_show_env_origin(
    run_cli: RunCli, config_home: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("SMARTPIPE_MODEL", "gpt-5.4-mini")
    _code, out, _err = run_cli(["config", "show"])
    assert "(env)" in out.splitlines()[0]


# --- bare config (non-TTY) ----------------------------------------------------


def test_bare_config_without_tty_is_setup_fault(run_cli: RunCli, config_home: Path) -> None:
    code, _out, err = run_cli(["config"], stdin="")
    assert code == 2
    assert "interactive and needs a terminal" in err
    assert "smartpipe config model ollama/qwen3:8b" in err


# --- the three-stage flow (unit, injected I/O) ------------------------------------


class _Recorder:
    def __init__(self) -> None:
        self.saved: Config | None = None
        self.said: list[str] = []

    def say(self, message: str) -> None:
        self.said.append(message)

    def save(self, config: Config) -> None:
        self.saved = config

    def transcript(self) -> str:
        return "\n".join(self.said)


class _Menu:
    """A scripted chooser that records every menu it was shown. An empty
    script means every menu answers with its preselected start row."""

    def __init__(self, picks: list[int | None] | None) -> None:
        self.picks = picks
        self.shown: list[tuple[str, tuple[str, ...], int]] = []

    def __call__(self, title: str, labels: tuple[str, ...], start: int) -> int | None:
        self.shown.append((title, labels, start))
        return start if self.picks is None else self.picks.pop(0)


async def _run_flow(
    *,
    current: Config | None = None,
    env: dict[str, str] | None = None,
    tags: tuple[str, ...] | None = None,
    login: bool = False,
    catalogs: Mapping[str, tuple[str, ...] | None] | None = None,
    embed_catalogs: Mapping[str, tuple[str, ...] | None] | None = None,
    chips: Mapping[str, ProbeChip] | None = None,
    picks: list[int | None] | None = None,
    answers: dict[str, str] | None = None,
    confirms: dict[str, bool] | None = None,
    connect: object = None,
    local_embed: bool = True,
    verify: object = None,
) -> tuple[Config, _Recorder, _Menu]:
    from collections.abc import Awaitable, Callable

    from smartpipe.config.picker import StageEntry

    rec = _Recorder()
    menu = _Menu(picks)
    held_answers = answers or {}
    held_confirms = confirms or {}

    async def probe() -> tuple[str, ...] | None:
        return tags

    async def fetch(provider: str) -> tuple[str, ...] | None:
        return (catalogs or {}).get(provider)

    async def fetch_embed(provider: str) -> tuple[str, ...] | None:
        return (embed_catalogs or {}).get(provider)

    def ask(question: str, default: str) -> str:
        return held_answers.get(question, default)

    def confirm(question: str, default: bool) -> bool:
        return held_confirms.get(question, default)

    assert connect is None or callable(connect)
    assert verify is None or callable(verify)
    typed_connect: Callable[[StageEntry], Awaitable[bool]] | None = connect  # type: ignore[assignment]
    typed_verify: Callable[[Config], Awaitable[None]] | None = verify  # type: ignore[assignment]
    result = await run_config_flow(
        current=current if current is not None else Config(),
        env=env if env is not None else {},
        probe=probe,
        login=lambda: login,
        fetch_catalog=fetch,
        fetch_embed_catalog=fetch_embed,
        chips=chips or {},
        now=_NOW,
        choose=menu,
        ask=ask,
        confirm=confirm,
        say=rec.say,
        save=rec.save,
        connect=typed_connect,
        local_embed_available=local_embed,
        run_verify=typed_verify,
    )
    return result, rec, menu


# TEXT provider rows (fresh config): 0 ollama · 1 openai (API key) · 2 openai (ChatGPT
# login) · 3 gemini · 4 anthropic · 5 mistral · 6 openrouter · 7 skip.
# EMBED rows: [pair?] then local · ollama · openai (API key) · gemini · mistral · jina,
# then keep-current (when set) or skip. OCR rows: keep · mistral · [vision] · typed · [unset].


async def test_flow_ollama_pick_pairs_a_detected_local_embedder() -> None:
    result, rec, menu = await _run_flow(
        tags=("nomic-embed-text", "llava", "qwen3:8b"),
        picks=[0, 0, 0, 0],  # text: ollama · llava — embed: the pair — ocr: skip
    )
    assert result.model == "ollama/llava"
    assert result.embed_model == "ollama/nomic-embed-text"  # detected tag wins
    assert rec.saved == result
    text_title, text_labels, _start = menu.shown[0]
    assert text_title == "Text model - pick a provider:"
    assert text_labels[0].startswith("ollama")
    assert "✓ local" in text_labels[0]
    assert text_labels[-1].startswith("skip")
    model_title, model_labels_shown, model_start = menu.shown[1]
    assert model_title == "Pick a model (ollama):"
    assert model_labels_shown[0] == "ollama/llava"  # embedders never offered as chat
    assert model_labels_shown[-1].startswith("type a model name instead")
    assert model_start == 0  # llava is the family-preferred cursor start
    embed_title, embed_labels, embed_start = menu.shown[2]
    assert embed_title.startswith("Embedding model")
    assert embed_labels[0] == "ollama/nomic-embed-text - paired with ollama"
    assert embed_start == 0  # the pair suggestion is PRESELECTED
    assert any("paired with ollama" in line for line in rec.said)
    assert rec.said[-1].strip().endswith('smartpipe map "translate to Spanish"')


async def test_flow_openai_catalog_pick_and_pairing() -> None:
    result, rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini", "o4-mini")},
        picks=[1, 0, 0, 0],
    )
    assert result.model == "openai/gpt-5.4-mini"
    assert result.embed_model == "openai/text-embedding-3-small"  # via the pair row
    assert rec.saved == result
    assert any("✓ model openai/gpt-5.4-mini" in line for line in rec.said)
    assert any("embed-model openai/text-embedding-3-small" in line for line in rec.said)
    text_labels = menu.shown[0][1]
    assert text_labels[1].startswith("openai (API key)")
    assert "✓ key" in text_labels[1]
    assert text_labels[2].startswith("openai (ChatGPT login)")


async def test_flow_never_overwrites_a_deliberate_embedder() -> None:
    result, rec, menu = await _run_flow(
        current=Config(embed_model="jina/jina-clip-v2"),
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 0, 6, 0],  # embed menu: NO pair row; index 6 = keep current
    )
    assert result.embed_model == "jina/jina-clip-v2"  # user intent outranks the pairing
    embed_labels = menu.shown[2][1]
    assert not any("paired with" in label for label in embed_labels)
    assert embed_labels[6] == "keep current: jina/jina-clip-v2"
    assert not any("paired" in line for line in rec.said)


async def test_flow_repairs_a_previously_paired_embedder() -> None:
    result, _rec, menu = await _run_flow(
        current=Config(embed_model="openai/text-embedding-3-small"),
        env={"GEMINI_API_KEY": "g"},
        catalogs={"gemini": ("gemini-3.1-flash-lite",)},
        picks=[3, 0, 0, 0],
    )
    assert result.model == "gemini/gemini-3.1-flash-lite"
    assert result.embed_model == "gemini/gemini-embedding-001"  # the old pair moves with us
    assert menu.shown[2][1][0] == "gemini/gemini-embedding-001 - paired with gemini"


async def test_flow_catalog_failure_degrades_to_typed_input() -> None:
    result, rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={},  # fetch returns None — the 403/timeout path
        picks=[1, 0, 0],
    )
    assert result.model == "openai/gpt-5.4-mini"  # the provider-prefixed example default
    assert len(menu.shown) == 3  # no model menu — typed input took over
    assert any("couldn't fetch the live catalog" in line for line in rec.said)
    assert any("openai/gpt-5.4-mini" in line and "OPENAI_API_KEY" in line for line in rec.said)


async def test_flow_type_it_entry_routes_to_typed_input() -> None:
    result, _rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 1, 0, 0],  # the model menu's last entry is "type a model name instead…"
        answers={"Default model?": "o4-mini"},
    )
    assert result.model == "openai/o4-mini"
    assert menu.shown[1][1][-1].startswith("type a model name instead")


async def test_flow_typed_junk_twice_is_a_usage_fault() -> None:
    from smartpipe.core.errors import UsageFault

    with pytest.raises(UsageFault):
        await _run_flow(
            env={"OPENAI_API_KEY": "sk-x"},
            catalogs={},
            picks=[1],
            answers={
                "Default model?": " ",
                "Default model? (that wasn't a model ref: no model given — "
                "try: --model ollama/qwen3:8b)": " ",
            },
        )


async def test_flow_backup_question_loops_the_picker_once() -> None:
    result, rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x", "GEMINI_API_KEY": "g"},
        catalogs={"openai": ("gpt-5.4-mini",), "gemini": ("gemini-3.1-flash-lite",)},
        picks=[1, 0, 1, 0, 0, 0],  # text · model · backup provider · backup model · embed · ocr
        confirms={"Add a backup model for provider outages?": True},
    )
    assert result.model == "openai/gpt-5.4-mini"
    assert result.fallback_model == "gemini/gemini-3.1-flash-lite"
    backup_title, backup_labels, _s = menu.shown[2]
    assert backup_title == "Pick a backup provider:"
    assert all("needs" not in label for label in backup_labels)  # connected only
    assert any("fallback-model gemini/gemini-3.1-flash-lite" in line for line in rec.said)


async def test_flow_backup_defaults_to_no() -> None:
    result, _rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 0, 0, 0],
    )
    assert result.fallback_model is None
    assert len(menu.shown) == 4  # text · model · embed · ocr — the picker never looped


async def test_flow_backup_refuses_an_embedder() -> None:
    result, rec, _menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 0, 0, 1, 0, 0],  # backup goes through "type it instead"
        answers={"Backup model?": "text-embedding-3-small"},
        confirms={"Add a backup model for provider outages?": True},
    )
    assert result.fallback_model is None
    assert any("embeds" in line and "must chat" in line for line in rec.said)


async def test_flow_cancel_everywhere_changes_nothing() -> None:
    current = Config(model="ollama/qwen3:8b")
    result, rec, _menu = await _run_flow(
        current=current, env={"OPENAI_API_KEY": "sk-x"}, picks=[None, None, None]
    )
    assert result == current
    assert rec.saved is None
    assert any("nothing changed" in line for line in rec.said)


async def test_flow_decline_save_stamps_nothing_but_still_offers_completions() -> None:
    offered: list[bool] = []
    rec = _Recorder()
    menu = _Menu([1, 0, 0, 0])

    async def probe() -> tuple[str, ...] | None:
        return None

    async def fetch(_provider: str) -> tuple[str, ...] | None:
        return ("gpt-5.4-mini",)

    async def fetch_embed(_provider: str) -> tuple[str, ...] | None:
        return None

    result = await run_config_flow(
        current=Config(),
        env={"OPENAI_API_KEY": "sk-x"},
        probe=probe,
        login=lambda: False,
        fetch_catalog=fetch,
        fetch_embed_catalog=fetch_embed,
        chips={},
        now=_NOW,
        choose=menu,
        ask=lambda _q, default: default,
        confirm=lambda question, default: False if question == "Save to config?" else default,
        say=rec.say,
        save=rec.save,
        offer_completions=lambda: offered.append(True),
    )
    assert result.model == "openai/gpt-5.4-mini"  # returned, but…
    assert rec.saved is None  # …never written
    assert any("Not saved" in line for line in rec.said)
    assert offered == [True]


async def test_flow_completions_come_before_the_try_it_bait() -> None:
    rec = _Recorder()
    menu = _Menu([0, 0, 0, 0])

    async def probe() -> tuple[str, ...] | None:
        return ("llava",)

    async def fetch(_provider: str) -> tuple[str, ...] | None:
        return None

    async def fetch_embed(_provider: str) -> tuple[str, ...] | None:
        return None

    await run_config_flow(
        current=Config(),
        env={},
        probe=probe,
        login=lambda: False,
        fetch_catalog=fetch,
        fetch_embed_catalog=fetch_embed,
        chips={},
        now=_NOW,
        choose=menu,
        ask=lambda _q, default: default,
        confirm=lambda _q, default: default,
        say=rec.say,
        save=rec.save,
        offer_completions=lambda: rec.say("<completions offer>"),
    )
    # completions BEFORE the try-it invitation: a paste-me command printed while
    # questions remain baits the paste into the next prompt (owner-hit)
    offer_at = rec.said.index("<completions offer>")
    try_it_at = next(i for i, line in enumerate(rec.said) if "Try it" in line)
    assert offer_at < try_it_at


async def test_flow_menu_badges_every_provider_even_disconnected() -> None:
    result, _rec, menu = await _run_flow(
        current=Config(), env={}, tags=None, picks=[None, None, None]
    )
    assert result == Config()
    text_labels = menu.shown[0][1]
    assert sum("needs key" in label for label in text_labels) == 5
    assert any("needs login" in label for label in text_labels)
    assert any("needs install" in label for label in text_labels)


async def test_flow_chips_annotate_probed_catalog_entries() -> None:
    chips = {"openai/gpt-5.4-mini": ProbeChip(sees=True, hears=False, ts=_NOW - 3 * 86_400)}
    _result, _rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini", "o4-mini")},
        chips=chips,
        picks=[1, 0, 0, 0],
    )
    model_labels_shown = menu.shown[1][1]
    assert model_labels_shown[0] == "openai/gpt-5.4-mini  (sees — probed 3d ago)"
    assert model_labels_shown[1] == "openai/o4-mini"  # no cache = no chips, no claims


async def test_flow_openrouter_picks_canonicalize_nested_names() -> None:
    result, _rec, _menu = await _run_flow(
        env={"OPENROUTER_API_KEY": "sk-or"},
        catalogs={"openrouter": ("x-ai/grok-4.5",)},
        picks=[6, 0, 5, 0],  # embed: no pairing for openrouter — skip stays a keypress away
    )
    assert result.model == "openrouter/x-ai/grok-4.5"
    assert result.embed_model is None  # no ratified pairing for openrouter


async def test_flow_ollama_without_chat_models_degrades_to_typed() -> None:
    result, rec, _menu = await _run_flow(
        tags=("nomic-embed-text",),  # daemon up, but only embedders installed
        picks=[0, 0, 0],
    )
    assert result.model == "ollama/qwen3:8b"  # the typed example default
    assert any("ollama pull qwen3:8b" in line for line in rec.said)


# --- inline connect (deliverable 1 dropped into a stage) ---------------------------------


async def test_flow_unconnected_provider_drops_into_connect_then_continues() -> None:
    env: dict[str, str] = {}
    connected: list[str] = []

    async def connect(entry: object) -> bool:
        from smartpipe.config.picker import StageEntry

        assert isinstance(entry, StageEntry)
        connected.append(entry.provider)
        env["MISTRAL_API_KEY"] = "mk-live"  # what the real connect does via the store
        return True

    result, _rec, _menu = await _run_flow(
        env=env,
        catalogs={"mistral": ("mistral-small-latest",)},
        picks=[5, 0, 0, 0],
        connect=connect,
    )
    assert connected == ["mistral"]
    assert result.model == "mistral/mistral-small-latest"  # the stage continued seamlessly
    assert result.embed_model == "mistral/mistral-embed"  # pair row followed the pick


async def test_flow_connect_declined_reshows_the_menu_with_fresh_badges() -> None:
    attempts: list[str] = []

    async def connect(entry: object) -> bool:
        from smartpipe.config.picker import StageEntry

        assert isinstance(entry, StageEntry)
        attempts.append(entry.provider)
        return False

    result, rec, menu = await _run_flow(
        env={},
        picks=[5, 7, 5, 6, 0],  # mistral declined, text again (skip); jina declined, skip; ocr
        connect=connect,
    )
    assert attempts == ["mistral", "jina"]  # embed menu index 5 = jina (no pair row)
    assert result == Config()
    assert rec.saved is None
    # the text menu was shown twice: once before, once after the declined connect
    text_menus = [shown for shown in menu.shown if shown[0].startswith("Text model")]
    assert len(text_menus) == 2


async def test_flow_without_connect_says_the_auth_door_and_skips() -> None:
    result, rec, _menu = await _run_flow(env={}, picks=[5, 6, 0])
    assert result == Config()
    assert any("smartpipe auth login mistral" in line for line in rec.said)


# --- the EMBED stage ----------------------------------------------------------------------


async def test_flow_embed_provider_pick_uses_the_embed_catalog() -> None:
    result, _rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        embed_catalogs={"openai": ("text-embedding-3-small", "text-embedding-3-large")},
        picks=[7, 2, 1, 0],  # text: skip · embed: openai → the large embedder · ocr: skip
    )
    assert result.model is None
    assert result.embed_model == "openai/text-embedding-3-large"
    embed_model_menu = menu.shown[2]
    assert embed_model_menu[0] == "Pick an embedding model (openai):"
    assert embed_model_menu[1][0] == "openai/text-embedding-3-small"


async def test_flow_embed_stage_has_no_chatgpt_and_offers_jina() -> None:
    _result, _rec, menu = await _run_flow(
        env={"JINA_API_KEY": "j"},
        picks=[7, 5, 0, 0],  # text: skip · embed: jina → jina-clip-v2 · ocr: skip
    )
    embed_labels = menu.shown[1][1]
    assert not any("ChatGPT" in label for label in embed_labels)
    assert any(label.startswith("jina") for label in embed_labels)


async def test_flow_embed_jina_pick_is_curated() -> None:
    result, _rec, menu = await _run_flow(
        env={"JINA_API_KEY": "j"},
        picks=[7, 5, 0, 0],
    )
    assert result.embed_model == "jina/jina-clip-v2"
    assert menu.shown[2][1][0] == "jina/jina-clip-v2"


async def test_flow_embed_local_row_vanishes_without_fastembed() -> None:
    _result, _rec, menu = await _run_flow(env={}, picks=[7, None, 0], local_embed=False)
    embed_labels = menu.shown[1][1]
    assert not any("built-in, on-device" in label for label in embed_labels)


# --- the OCR stage --------------------------------------------------------------------


async def test_flow_ocr_mistral_pick_sets_the_dedicated_wire() -> None:
    result, rec, _menu = await _run_flow(
        env={"MISTRAL_API_KEY": "mk"},
        catalogs={"mistral": ("mistral-small-latest",)},
        picks=[5, 0, 0, 1],
    )
    assert result.ocr_model == "mistral/mistral-ocr-latest"
    assert any("✓ ocr-model mistral/mistral-ocr-latest" in line for line in rec.said)


async def test_flow_ocr_vision_pick_reuses_the_chat_model() -> None:
    result, _rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 0, 0, 2],
    )
    assert result.ocr_model == "openai/gpt-5.4-mini"
    ocr_labels = menu.shown[3][1]
    assert "extract-the-text" in ocr_labels[2]


async def test_flow_ocr_explains_what_it_changes_and_skips_in_one_keypress() -> None:
    _result, rec, menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 0, 0, 0],
    )
    assert any("document parsing at ingestion" in line for line in rec.said)
    ocr_title, ocr_labels, ocr_start = menu.shown[3]
    assert ocr_title == "Document OCR - optional:"
    assert ocr_start == 0 and ocr_labels[0].startswith("skip - ")  # Enter = skip


async def test_flow_ocr_unset_clears_the_role() -> None:
    current = Config(model="openai/gpt-5.4-mini", ocr_model="mistral/mistral-ocr-latest")
    result, rec, menu = await _run_flow(
        current=current,
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[7, 7, 4],  # text: keep · embed: skip past the pair · ocr: unset
    )
    assert result.ocr_model is None
    assert any("ocr-model unset" in line for line in rec.said)
    assert menu.shown[2][1][4] == "unset - back to the built-in local extraction"


async def test_flow_ocr_mistral_without_key_asks_to_connect_first() -> None:
    connected: list[str] = []

    async def connect(entry: object) -> bool:
        from smartpipe.config.picker import StageEntry

        assert isinstance(entry, StageEntry)
        connected.append(entry.provider)
        return True

    result, _rec, _menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 0, 0, 1],
        connect=connect,
    )
    assert connected == ["mistral"]
    assert result.ocr_model == "mistral/mistral-ocr-latest"


# --- idempotence + the exit-probe hook -----------------------------------------------


async def test_flow_rerun_all_enter_changes_nothing() -> None:
    current = Config(model="ollama/llava", embed_model="ollama/nomic-embed-text")
    result, rec, menu = await _run_flow(
        current=current,
        tags=("llava", "nomic-embed-text"),
        picks=None,  # every menu answers with its preselected row
    )
    assert result == current
    assert rec.saved is None
    assert any("nothing changed" in line for line in rec.said)
    text_menu = menu.shown[0]
    assert text_menu[1][text_menu[2]] == "keep current: ollama/llava"  # preselected
    embed_menu = menu.shown[1]
    assert embed_menu[1][embed_menu[2]] == "keep current: ollama/nomic-embed-text"


async def test_flow_model_menu_preselects_the_current_model() -> None:
    current = Config(model="openai/o4-mini")
    _result, _rec, menu = await _run_flow(
        current=current,
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini", "o4-mini")},
        picks=[1, 1, 0, 0],
    )
    model_menu = menu.shown[1]
    assert model_menu[2] == 1  # cursor starts on openai/o4-mini


async def test_flow_verify_hook_runs_after_save_with_the_result() -> None:
    verified: list[Config] = []
    said_order: list[str] = []

    async def verify(config: Config) -> None:
        verified.append(config)
        said_order.append("<verify>")

    result, rec, _menu = await _run_flow(
        env={"OPENAI_API_KEY": "sk-x"},
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 0, 0, 0],
        verify=verify,
    )
    assert verified == [result]
    assert rec.saved == result  # saved BEFORE the probe spends anything


async def test_flow_verify_hook_still_offered_on_a_no_change_rerun() -> None:
    current = Config(model="ollama/llava", embed_model="ollama/nomic-embed-text")
    verified: list[Config] = []

    async def verify(config: Config) -> None:
        verified.append(config)

    result, _rec, _menu = await _run_flow(
        current=current, tags=("llava", "nomic-embed-text"), picks=None, verify=verify
    )
    assert verified == [current]
    assert result == current


def test_set_embed_model_mistral(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "embed-model", "mistral-embed"])
    assert code == 0
    assert out.strip() == "embed-model set to mistral/mistral-embed"
    assert load_config(config_home).embed_model == "mistral/mistral-embed"


def test_profile_switch_and_list(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    code, _out, err = run_cli(["config", "profile", "local"])
    assert code == 0
    assert "profile 'local' active — model: ollama/gemma-4-e2b" in err
    code, out, _err = run_cli(["config", "profile"])
    assert code == 0
    assert "* local" in out
    assert "  openai" in out


def test_profile_unknown_name_lists_known(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))  # the windows config root (D09)
    code, _out, err = run_cli(["config", "profile", "yolo"])
    assert code == 2
    assert "profile 'yolo' doesn't exist" in err


# --- the wizard's completions offer ----------------------------------------------


def _offer(
    home: Path,
    shell: str,
    *,
    accept: bool = True,
    confirmed: list[str] | None = None,
) -> list[str]:
    from smartpipe.cli.config_cmd import offer_shell_completions

    said: list[str] = []

    def confirm(question: str) -> bool:
        if confirmed is not None:
            confirmed.append(question)
        return accept

    offer_shell_completions(env={"SHELL": shell}, home=home, confirm=confirm, say=said.append)
    return said


def test_completions_offer_writes_once_and_discloses(tmp_path: Path) -> None:
    said = _offer(tmp_path, "/bin/zsh")
    rc = tmp_path / ".zshrc"
    line = 'eval "$(_SMARTPIPE_COMPLETE=zsh_source smartpipe)"'
    assert rc.read_text(encoding="utf-8") == line + "\n"
    assert any(".zshrc" in message and line in message for message in said)  # disclosed


def test_completions_offer_is_idempotent(tmp_path: Path) -> None:
    _offer(tmp_path, "/bin/zsh")
    confirmed: list[str] = []
    said = _offer(tmp_path, "/bin/zsh", confirmed=confirmed)
    assert confirmed == []  # already installed: no nagging
    assert said == []
    rc_text = (tmp_path / ".zshrc").read_text(encoding="utf-8")
    assert rc_text.count("_SMARTPIPE_COMPLETE") == 1  # never appended twice


def test_completions_offer_decline_writes_nothing(tmp_path: Path) -> None:
    said = _offer(tmp_path, "/usr/bin/bash", accept=False)
    assert not (tmp_path / ".bashrc").exists()
    assert any("Skipped" in message for message in said)


def test_completions_offer_appends_after_existing_content(tmp_path: Path) -> None:
    rc = tmp_path / ".bashrc"
    rc.write_text("export PATH=$PATH:~/bin", encoding="utf-8")  # no trailing newline
    _offer(tmp_path, "/bin/bash")
    text = rc.read_text(encoding="utf-8")
    assert text.startswith("export PATH=$PATH:~/bin\n")
    assert text.endswith('eval "$(_SMARTPIPE_COMPLETE=bash_source smartpipe)"\n')


def test_completions_offer_unknown_shell_stays_silent(tmp_path: Path) -> None:
    confirmed: list[str] = []
    said = _offer(tmp_path, "/usr/bin/fish", confirmed=confirmed)
    assert said == [] and confirmed == []
    assert list(tmp_path.iterdir()) == []  # never touches files


# --- config update-check --------------------------------------------------------


def test_update_check_off_persists(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "update-check", "off"])
    assert code == 0
    assert "update-check off" in out
    assert load_config(config_home).update_check is False


def test_update_check_on_persists(run_cli: RunCli, config_home: Path) -> None:
    run_cli(["config", "update-check", "off"])
    code, out, _err = run_cli(["config", "update-check", "on"])
    assert code == 0
    assert "update-check on" in out
    assert load_config(config_home).update_check is True


def test_update_check_junk_value_is_usage_error(run_cli: RunCli, config_home: Path) -> None:
    code, _out, err = run_cli(["config", "update-check", "maybe"])
    assert code == 64
    assert "on" in err and "off" in err


def test_set_ocr_model(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "ocr-model", "mistral-ocr-latest"])
    assert code == 0
    assert "ocr-model set to mistral/mistral-ocr-latest" in out
    assert load_config(config_home).ocr_model == "mistral/mistral-ocr-latest"


def test_set_media_embed_model(run_cli: RunCli, config_home: Path) -> None:
    code, out, _err = run_cli(["config", "media-embed-model", "jina/jina-clip-v2"])
    assert code == 0
    assert "media-embed-model set to jina/jina-clip-v2" in out
    assert load_config(config_home).media_embed_model == "jina/jina-clip-v2"
