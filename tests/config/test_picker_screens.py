"""Golden pins for the picker's non-interactive screens (plan/ux.md).

Each scenario drives ``run_provider_picker`` through the NUMBERED fallback
(the exact surface a TERM=dumb or piped-key user sees) with every prompt
answered by its default, and pins the full transcript. Refresh with
``make golden`` after an intentional change.
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest

from smartpipe.cli.config_cmd import run_provider_picker
from smartpipe.config.picker import ProbeChip
from smartpipe.config.store import Config
from smartpipe.io.arrow_menu import numbered_choose

GOLDEN = Path(__file__).parent.parent / "golden" / "screens"
_NOW = 1_751_900_000.0


async def _transcript(
    *,
    env: dict[str, str],
    tags: tuple[str, ...] | None,
    catalogs: dict[str, tuple[str, ...] | None],
    chips: dict[str, ProbeChip] | None = None,
) -> str:
    said: list[str] = []

    async def probe() -> tuple[str, ...] | None:
        return tags

    async def fetch(provider: str) -> tuple[str, ...] | None:
        return catalogs.get(provider)

    def ask(_question: str, default: str) -> str:
        return default  # Enter-Enter-Enter must work (ux.md walkthrough rule)

    def choose(title: str, labels: tuple[str, ...], start: int) -> int | None:
        return numbered_choose(title, labels, start, ask=ask, say=said.append)

    await run_provider_picker(
        current=Config(),
        env=env,
        probe=probe,
        login=lambda: False,
        fetch_catalog=fetch,
        chips=chips or {},
        now=_NOW,
        choose=choose,
        ask=ask,
        confirm=lambda _question, default: default,
        say=said.append,
        save=lambda _config: None,
    )
    return "\n".join(said) + "\n"


async def test_picker_walkthrough_screen_matches_golden() -> None:
    rendered = await _transcript(
        env={"OPENAI_API_KEY": "sk-x"},
        tags=("llava", "nomic-embed-text"),
        catalogs={},
        chips={"ollama/llava": ProbeChip(sees=True, hears=False, ts=_NOW - 2 * 86_400)},
    )
    _assert_golden("config_picker_walkthrough", rendered)


async def test_picker_no_providers_screen_matches_golden() -> None:
    rendered = await _transcript(env={}, tags=None, catalogs={})
    _assert_golden("config_picker_no_providers", rendered)


async def test_picker_typed_fallback_screen_matches_golden() -> None:
    rendered = await _transcript(
        env={"OPENAI_API_KEY": "sk-x"},
        tags=None,
        catalogs={"openai": None},  # the catalog fetch failed — typed input takes over
    )
    _assert_golden("config_picker_typed_fallback", rendered)


def _assert_golden(name: str, rendered: str) -> None:
    rendered = _strip_ansi(rendered)  # goldens pin PLAIN text; styling is never contract (D42)
    path = GOLDEN / f"{name}.txt"
    if os.environ.get("UPDATE_GOLDEN"):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(rendered, encoding="utf-8")
    if not path.exists():
        pytest.fail(f"golden '{name}' missing; create it with: make golden")
    assert rendered == path.read_text(encoding="utf-8"), (
        f"screen '{name}' drifted from its golden; if intended, run: make golden"
    )


def _strip_ansi(text: str) -> str:
    import re

    return re.sub(r"\x1b\[[0-9;]*m", "", text)
