"""Golden pins for the three-stage flow's non-interactive screens (plan/ux.md).

Each scenario drives ``run_config_flow`` through the NUMBERED fallback
(the exact surface a TERM=dumb or piped-key user sees) with every prompt
answered by its default, and pins the full transcript. Refresh with
``make golden`` after an intentional change.
"""

from __future__ import annotations

from smartpipe.cli.config_cmd import run_config_flow
from smartpipe.config.picker import ChipSources, ProbeChip, RegistryCaps
from smartpipe.config.store import Config
from smartpipe.io.arrow_menu import numbered_choose
from tests.helpers.golden import assert_golden

_NOW = 1_751_900_000.0


async def _transcript(
    *,
    env: dict[str, str],
    tags: tuple[str, ...] | None,
    catalogs: dict[str, tuple[str, ...] | None],
    embed_catalogs: dict[str, tuple[str, ...] | None] | None = None,
    chips: dict[str, ProbeChip] | None = None,
    registry: dict[str, RegistryCaps] | None = None,
    picks: list[int | None] | None = None,
) -> str:
    said: list[str] = []

    async def probe() -> tuple[str, ...] | None:
        return tags

    async def fetch(provider: str) -> tuple[str, ...] | None:
        return catalogs.get(provider)

    async def fetch_embed(provider: str) -> tuple[str, ...] | None:
        return (embed_catalogs or {}).get(provider)

    def ask(_question: str, default: str) -> str:
        return default  # Enter-Enter-Enter must work (ux.md walkthrough rule)

    scripted = list(picks) if picks is not None else None

    def choose(title: str, labels: tuple[str, ...], start: int) -> int | None:
        picked = start if scripted is None or not scripted else scripted.pop(0)
        if picked is None:
            return None
        return numbered_choose(
            title,
            labels,
            start,
            ask=lambda _question, _default: str(picked + 1),
            say=said.append,
        )

    await run_config_flow(
        current=Config(),
        env=env,
        probe=probe,
        login=lambda: False,
        fetch_catalog=fetch,
        fetch_embed_catalog=fetch_embed,
        chips=ChipSources(probed=chips or {}, registry=registry or {}, declared={}),
        now=_NOW,
        choose=choose,
        ask=ask,
        confirm=lambda _question, default: default,
        say=said.append,
        save=lambda _config: None,
    )
    return "\n".join(said) + "\n"


async def test_flow_walkthrough_screen_matches_golden() -> None:
    rendered = await _transcript(
        env={"OPENAI_API_KEY": "sk-x"},
        tags=("llava", "nomic-embed-text"),
        catalogs={},
        chips={"ollama/llava": ProbeChip(sees=True, hears=False, ts=_NOW - 2 * 86_400)},
    )
    assert_golden("config_flow_walkthrough", rendered)


async def test_flow_no_providers_screen_matches_golden() -> None:
    rendered = await _transcript(env={}, tags=None, catalogs={})
    assert_golden("config_flow_no_providers", rendered)


async def test_flow_typed_fallback_screen_matches_golden() -> None:
    rendered = await _transcript(
        env={"OPENAI_API_KEY": "sk-x"},
        tags=None,
        catalogs={"openai": None},  # the catalog fetch failed — typed input takes over
    )
    assert_golden("config_flow_typed_fallback", rendered)


async def test_flow_ocr_curated_screen_matches_golden() -> None:
    # models.dev tags all of these image-input, but the OCR stage curates like
    # the chat stage: the noise variants (chatgpt-image / gpt-realtime) and the
    # dated snapshot drop out, and the survivors lead with one model per
    # provider so the openai list can't bury anthropic/gemini under the cap.
    rendered = await _transcript(
        env={"OPENAI_API_KEY": "sk-x"},
        tags=None,
        catalogs={"openai": None},  # typed fallback pins the chat model deterministically
        registry={
            "openai/chatgpt-image-latest": RegistryCaps(image=True, audio=False),
            "openai/gpt-realtime-2.1": RegistryCaps(image=True, audio=False),
            "openai/gpt-4o-2024-08-06": RegistryCaps(image=True, audio=False),
            "openai/gpt-4o": RegistryCaps(image=True, audio=False),
            "openai/gpt-5.4": RegistryCaps(image=True, audio=False),
            "anthropic/claude-opus-4-8": RegistryCaps(image=True, audio=False),
            "gemini/gemini-3.1-flash": RegistryCaps(image=True, audio=False),
            "openai/o4-mini": RegistryCaps(image=False, audio=False),  # text-only: dropped
        },
    )
    assert_golden("config_flow_ocr_curated", rendered)


async def test_flow_back_navigation_screen_matches_golden() -> None:
    rendered = await _transcript(
        env={"OPENAI_API_KEY": "sk-x"},
        tags=None,
        catalogs={"openai": ("gpt-5.4-mini",)},
        picks=[1, 0, 8, 7, 6, 0, 0],
    )
    assert_golden("config_flow_back_navigation", rendered)
