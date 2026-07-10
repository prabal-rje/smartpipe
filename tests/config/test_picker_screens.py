"""Golden pins for the three-stage flow's non-interactive screens (plan/ux.md).

Each scenario drives ``run_config_flow`` through the NUMBERED fallback
(the exact surface a TERM=dumb or piped-key user sees) with every prompt
answered by its default, and pins the full transcript. Refresh with
``make golden`` after an intentional change.
"""

from __future__ import annotations

from smartpipe.cli.config_cmd import run_config_flow
from smartpipe.config.picker import ProbeChip
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

    def choose(title: str, labels: tuple[str, ...], start: int) -> int | None:
        return numbered_choose(title, labels, start, ask=ask, say=said.append)

    await run_config_flow(
        current=Config(),
        env=env,
        probe=probe,
        login=lambda: False,
        fetch_catalog=fetch,
        fetch_embed_catalog=fetch_embed,
        chips=chips or {},
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
