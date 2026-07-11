from __future__ import annotations

import re
import subprocess
import sys

import pytest

from smartpipe.io.richui import Cell, UiStyle, render_grid, render_text


def _strip_ansi(text: str) -> str:
    return re.sub(r"\x1b\[[0-9;]*m", "", text)


def test_import_does_not_load_rich() -> None:
    script = "import sys; import smartpipe.io.richui; assert 'rich' not in sys.modules"
    subprocess.run([sys.executable, "-c", script], check=True)


def test_render_grid_aligns_wide_text_without_trailing_newline() -> None:
    rendered = render_grid(
        (
            (Cell("model", UiStyle.DIM), Cell("ollama/日本語"), Cell("(config)", UiStyle.DIM)),
            (Cell("output", UiStyle.DIM), Cell("auto"), Cell("(default)", UiStyle.DIM)),
        ),
        color=False,
    )
    assert rendered == "model   ollama/日本語  (config)\noutput  auto           (default)"


def test_render_text_emits_ansi_only_when_color_is_enabled() -> None:
    spans = (Cell("error:", UiStyle.BAD), Cell(" broken"))
    plain = render_text(spans, color=False)
    colored = render_text(spans, color=True)
    assert plain == "error: broken"
    assert "\x1b[" in colored
    assert _strip_ansi(colored) == plain


def test_render_grid_colored_form_strips_to_the_plain_form() -> None:
    rows = ((Cell("[model]", UiStyle.DIM), Cell("ollama/日本語", UiStyle.GOOD)),)
    plain = render_grid(rows, color=False)
    colored = render_grid(rows, color=True)
    assert "[model]" in colored  # literal brackets are never treated as markup
    assert _strip_ansi(colored) == plain


def test_term_dumb_does_not_shrink_an_explicit_unwrapped_layout(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("TERM", "dumb")
    rows = ((Cell("model", UiStyle.DIM), Cell("x" * 90), Cell("(default)", UiStyle.DIM)),)
    plain = render_grid(rows, color=False)
    colored = render_grid(rows, color=True)
    assert "…" not in _strip_ansi(colored)
    assert _strip_ansi(colored) == plain


def test_render_grid_rejects_ragged_rows() -> None:
    with pytest.raises(AssertionError, match="same number of cells"):
        render_grid(((Cell("a"), Cell("b")), (Cell("c"),)), color=False)


def test_render_grid_handles_no_rows() -> None:
    assert render_grid((), color=False) == ""


def test_render_grid_rejects_mismatched_column_widths() -> None:
    with pytest.raises(AssertionError, match="widths must match"):
        render_grid(((Cell("a"), Cell("b")),), color=False, column_widths=(1,))
