"""The readable verb (wave 2, item 25): the explicit human door."""

from __future__ import annotations

import io
import json
from collections.abc import Mapping
from pathlib import Path

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.io.render import MediaLines
from smartpipe.verbs.readable import ReadableRequest, run_readable
from tests.conftest import RunCli


async def _run(stdin_text: str, **kw: object) -> tuple[ExitCode, str]:
    out = io.StringIO()
    code = await run_readable(
        ReadableRequest(**kw),  # type: ignore[arg-type]
        stdin=io.StringIO(stdin_text),
        stdout=out,
    )
    return code, out.getvalue()


async def test_records_render_as_blocks_with_blank_separators() -> None:
    rows = [json.dumps({"vendor": "Acme", "total": 5}), json.dumps({"vendor": "Bar", "total": 7})]
    code, out = await _run("\n".join(rows) + "\n")
    assert code is ExitCode.OK
    assert out == "vendor: Acme\ntotal: 5\n\nvendor: Bar\ntotal: 7\n\n"


async def test_text_lines_pass_through_unchanged() -> None:
    code, out = await _run("just a line\n")
    assert code is ExitCode.OK
    assert out == "just a line\n\n"


async def test_spine_shows_by_default_and_bare_drops_it() -> None:
    row = json.dumps({"result": "hola", "__source": {"path": "-", "as": "jsonl", "line": 1}})
    _code, shown = await _run(row + "\n")
    assert "__source:" in shown
    _code, bare = await _run(row + "\n", bare=True)
    assert "__source" not in bare
    assert bare == "result: hola\n\n"


async def test_ranking_stamps_render_dimmed_in_the_spine() -> None:
    # item 76: a top_k __score is tool metadata — readable dims it at the block's bottom
    row = json.dumps({"__score": 0.91, "text": "hit"})
    _code, out = await _run(row + "\n", color=True)
    assert out == "\x1b[2mtext:\x1b[0m hit\n\x1b[2m\x1b[2m__score:\x1b[0m 0.91\x1b[0m\n\n"


async def test_full_disables_truncation() -> None:
    row = json.dumps({"body": "x" * 450})
    _code, clipped = await _run(row + "\n")
    assert "(+50 chars)" in clipped
    _code, full = await _run(row + "\n", full=True)
    assert "x" * 450 in full


async def test_plain_layout_without_color() -> None:
    row = json.dumps({"a": 1})
    _code, out = await _run(row + "\n", color=False)
    assert "\x1b[" not in out  # no ANSI when stdout isn't a terminal


# --- media previews (injected hook — io/preview builds the real one) ---------------

_MEDIA_ROW = json.dumps(
    {"result": "hi", "__media": {"kind": "image", "mime": "image/png", "data_b64": "A" * 64}}
)


async def _run_with_hook(stdin_text: str, hook: MediaLines, **kw: object) -> str:
    out = io.StringIO()
    await run_readable(
        ReadableRequest(**kw),  # type: ignore[arg-type]
        stdin=io.StringIO(stdin_text),
        stdout=out,
        media_lines=hook,
    )
    return out.getvalue()


async def test_media_preview_renders_through_the_injected_hook() -> None:
    def fake_preview(record: Mapping[str, object]) -> list[str]:
        return ["  [thumbnail]"]

    out = await _run_with_hook(_MEDIA_ROW + "\n", fake_preview, color=True)
    assert "\n  [thumbnail]\n" in out
    media_line, preview_line = out.splitlines()[1:3]
    assert "__media:" in media_line
    assert preview_line == "  [thumbnail]"


async def test_bare_drops_media_so_the_hook_never_fires() -> None:
    def explode(record: object) -> list[str]:
        raise AssertionError("--bare strips __media; no preview call")

    out = await _run_with_hook(_MEDIA_ROW + "\n", explode, bare=True)
    assert out == "result: hi\n\n"


async def test_without_the_hook_media_rows_render_exactly_as_today() -> None:
    _code, out = await _run(_MEDIA_ROW + "\n")
    assert out == "result: hi\n__media: image/png (48 B)\n\n"


def test_piped_readable_cli_is_byte_identical_with_previews_on(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """stdout is sacred: through the real CLI, a piped (non-TTY) readable never
    grows preview bytes — the default-on config changes nothing off-terminal."""
    monkeypatch.setenv("XDG_CONFIG_HOME", str(tmp_path))
    monkeypatch.setenv("APPDATA", str(tmp_path))
    code, out, _err = run_cli(["readable"], stdin=_MEDIA_ROW + "\n")
    assert code == 0
    assert out == "result: hi\n__media: image/png (48 B)\n\n"
