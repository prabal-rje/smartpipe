"""The chart verb: counts in, bars out, SVG on request. Free.

Two terminal voices (D48): a real TTY with color gets plotext canvases; piped
or NO_COLOR output stays plain ASCII — aligned labels, ``#`` bars, exact
counts — so downstream tools can keep parsing it.
"""

from __future__ import annotations

import io
import re
from typing import TYPE_CHECKING

import pytest

from smartpipe.core.errors import ExitCode
from smartpipe.engine.chart import render_bars
from smartpipe.verbs.chart import ChartRequest, run_chart

if TYPE_CHECKING:
    from pathlib import Path

NDJSON = (
    '{"label": "bug"}\n{"label": "bug"}\n{"label": "feature"}\n{"label": "bug"}\n{"other": 1}\n'
)

_ANSI = re.compile(r"\x1b\[[0-9;]*m")


def _plain(text: str) -> str:
    return _ANSI.sub("", text)


def test_field_tally_renders_ranked_bars() -> None:
    out = io.StringIO()
    code = run_chart(ChartRequest(field="label"), stdin=io.StringIO(NDJSON), stdout=out)
    assert code is ExitCode.OK
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("bug")
    assert lines[0].rstrip().endswith("3")
    assert "#" in lines[0]
    assert len(lines[0].split("#")) > len(lines[1].split("#"))  # widest bar wins
    assert any(line.startswith("(missing)") for line in lines)  # honest about gaps


def test_piped_output_is_plain_ascii_and_pinned() -> None:
    """The piped contract: pure ASCII, no ANSI, label + bar + exact count."""
    out = io.StringIO()
    run_chart(ChartRequest(field="label"), stdin=io.StringIO(NDJSON), stdout=out)
    text = out.getvalue()
    assert text.isascii()
    assert "\x1b" not in text
    assert text == (
        "bug        ######################################## 3\n"
        "feature    ############# 1\n"
        "(missing)  ############# 1\n"
    )


def test_plain_lines_tally_whole_lines() -> None:
    out = io.StringIO()
    code = run_chart(ChartRequest(), stdin=io.StringIO("a\nb\na\n"), stdout=out)
    assert code is ExitCode.OK
    assert out.getvalue().splitlines()[0].startswith("a")


def test_top_caps_and_notes(capsys: object) -> None:
    out = io.StringIO()
    many = "".join(f'{{"label": "kind-{i}"}}\n' for i in range(30))
    run_chart(ChartRequest(field="label", top=5), stdin=io.StringIO(many), stdout=out)
    assert len(out.getvalue().splitlines()) == 5


def test_save_writes_a_standalone_svg(tmp_path: Path) -> None:
    out = io.StringIO()
    target = tmp_path / "labels.svg"
    code = run_chart(
        ChartRequest(field="label", save=target, title="Ticket labels"),
        stdin=io.StringIO(NDJSON),
        stdout=out,
    )
    assert code is ExitCode.OK
    svg = target.read_text(encoding="utf-8")
    assert "<svg" in svg
    assert "Ticket labels" in svg  # the title survives as real text
    assert "bug" in svg and "feature" in svg  # labels stay searchable text
    assert ">3<" in svg  # the count is annotated at the bar's end


def test_save_writes_a_png_by_extension(tmp_path: Path) -> None:
    out = io.StringIO()
    target = tmp_path / "labels.png"
    code = run_chart(
        ChartRequest(field="label", save=target),
        stdin=io.StringIO(NDJSON),
        stdout=out,
    )
    assert code is ExitCode.OK
    payload = target.read_bytes()
    assert payload.startswith(b"\x89PNG\r\n\x1a\n")
    assert len(payload) > 1_000  # a real raster, not a stub


def test_save_rejects_unknown_extensions() -> None:
    from pathlib import Path

    from smartpipe.core.errors import UsageFault

    with pytest.raises(UsageFault, match=r"SVG or PNG"):
        run_chart(
            ChartRequest(field="label", save=Path("out.pdf")),
            stdin=io.StringIO(NDJSON),
            stdout=io.StringIO(),
        )


def test_saved_svg_escapes_markup_in_labels(tmp_path: Path) -> None:
    run_chart(
        ChartRequest(field="label", save=tmp_path / "x.svg"),
        stdin=io.StringIO('{"label": "<script>"}\n'),
        stdout=io.StringIO(),
    )
    svg = (tmp_path / "x.svg").read_text(encoding="utf-8")
    assert "<script>" not in svg  # matplotlib escapes text nodes


def test_saved_charts_are_deterministic() -> None:
    from smartpipe.engine.chart import render_figure

    counts = [("bug", 3), ("feature", 1)]
    assert render_figure(counts, title="T", fmt="svg") == render_figure(
        counts, title="T", fmt="svg"
    )
    assert render_figure(counts, title=None, fmt="png") == render_figure(
        counts, title=None, fmt="png"
    )


def test_saved_chart_of_nothing_stays_honest() -> None:
    from smartpipe.engine.chart import render_figure, render_figure_panels, render_figure_timeseries

    assert b"(nothing)" in render_figure([], title=None, fmt="svg")
    assert b"(nothing)" in render_figure_panels([("facet", [])], title=None, fmt="svg")
    assert b"(nothing)" in render_figure_timeseries([], title=None, fmt="svg")


def test_save_without_matplotlib_is_a_setup_fault(monkeypatch: pytest.MonkeyPatch) -> None:
    import sys

    from smartpipe.core.errors import SetupFault
    from smartpipe.engine.chart import render_figure

    monkeypatch.setitem(sys.modules, "matplotlib", None)  # import now raises ImportError
    with pytest.raises(SetupFault, match="reinstall smartpipe"):
        render_figure([("bug", 1)], title=None, fmt="svg")


def test_bars_render_empty_input_honestly() -> None:
    from smartpipe.engine.chart import render_timeseries_tty

    assert render_bars([]) == "(nothing to chart)"
    assert render_timeseries_tty([], width=60) == "(nothing to chart)"


# --- TTY canvases via plotext (D48) --------------------------------------------------


def test_tty_bars_render_via_plotext_cyan() -> None:
    out = io.StringIO()
    code = run_chart(
        ChartRequest(field="label", color=True, width=60),
        stdin=io.StringIO(NDJSON),
        stdout=out,
    )
    assert code is ExitCode.OK
    text = out.getvalue()
    assert "\x1b[38;5;6m" in text  # cyan bars — the CLI accent
    plain = _plain(text)
    assert "bug" in plain and "feature" in plain and "(missing)" in plain
    assert "3" in plain and ".00" not in plain  # counts are integers, not 3.00
    assert "▇" in plain  # plotext draws the bars


def test_tty_bars_respect_the_terminal_width() -> None:
    out = io.StringIO()
    run_chart(
        ChartRequest(field="label", color=True, width=60),
        stdin=io.StringIO(NDJSON),
        stdout=out,
    )
    assert all(len(line) <= 60 for line in _plain(out.getvalue()).splitlines())


def test_tty_empty_input_stays_honest() -> None:
    out = io.StringIO()
    run_chart(ChartRequest(field="label", color=True), stdin=io.StringIO(""), stdout=out)
    assert out.getvalue() == "(nothing to chart)\n"


# --- facets (D38/12) -----------------------------------------------------------------


def test_facets_stack_sections_in_one_pass() -> None:
    out = io.StringIO()
    ndjson = (
        '{"label": "bug", "sev": "high"}\n{"label": "bug", "sev": "low"}\n{"label": "feature"}\n'
    )
    code = run_chart(ChartRequest(facets=("label", "sev")), stdin=io.StringIO(ndjson), stdout=out)
    assert code is ExitCode.OK
    text = out.getvalue()
    assert "── label " in text and "── sev " in text
    assert text.index("── label") < text.index("── sev")
    assert "(missing)" in text  # the feature row has no sev — honest gap


def test_facets_tty_draw_one_canvas_per_section() -> None:
    out = io.StringIO()
    ndjson = '{"label": "bug", "sev": "high"}\n{"label": "bug", "sev": "low"}\n'
    run_chart(
        ChartRequest(facets=("label", "sev"), color=True, width=60),
        stdin=io.StringIO(ndjson),
        stdout=out,
    )
    text = out.getvalue()
    assert "── label " in _plain(text) and "── sev " in _plain(text)
    assert text.count("\x1b[38;5;6m") >= 2  # cyan bars in both sections


def test_facet_with_field_is_a_usage_fault() -> None:
    import pytest

    from smartpipe.core.errors import UsageFault

    with pytest.raises(UsageFault, match="--facet replaces"):
        run_chart(
            ChartRequest(field="label", facets=("sev",)),
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
        )


def test_facet_svg_has_both_panels(tmp_path: object) -> None:
    from pathlib import Path

    assert isinstance(tmp_path, Path)
    out = io.StringIO()
    target = tmp_path / "facets.svg"
    run_chart(
        ChartRequest(facets=("label", "sev"), save=target, title="Tickets"),
        stdin=io.StringIO('{"label": "bug", "sev": "high"}\n'),
        stdout=out,
    )
    svg = target.read_text(encoding="utf-8")
    assert svg.count("label") >= 1 and svg.count("sev") >= 1
    assert "Tickets" in svg


# --- time buckets (D38/13) -----------------------------------------------------------


def test_by_time_is_chronological_and_zero_filled() -> None:
    out = io.StringIO()
    ndjson = (
        '{"ts": "2025-01-01T14:05:00Z"}\n'
        '{"ts": "2025-01-01T16:10:00Z"}\n'
        '{"ts": "2025-01-01T14:40:00Z"}\n'
        '{"ts": "not a time"}\n'
    )
    import contextlib

    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_chart(ChartRequest(by_time="ts:1h"), stdin=io.StringIO(ndjson), stdout=out)
    assert code is ExitCode.OK
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("14:00") and lines[0].rstrip().endswith("2")
    assert lines[1].startswith("15:00") and lines[1].rstrip().endswith("0")  # the gap is signal
    assert lines[2].startswith("16:00")
    assert "unparseable 'ts'" in err.getvalue()
    assert out.getvalue().isascii()  # piped time series stays plain


def test_by_time_tty_draws_a_green_time_axis() -> None:
    out = io.StringIO()
    ndjson = (
        '{"ts": "2025-01-01T14:05:00Z"}\n'
        '{"ts": "2025-01-01T16:10:00Z"}\n'
        '{"ts": "2025-01-01T14:40:00Z"}\n'
    )
    run_chart(
        ChartRequest(by_time="ts:1h", color=True, width=70),
        stdin=io.StringIO(ndjson),
        stdout=out,
    )
    text = out.getvalue()
    assert "\x1b[38;5;2m" in text  # green bars — time series voice
    plain = _plain(text)
    assert "14:00" in plain and "16:00" in plain
    assert all(len(line) <= 70 for line in plain.splitlines())


def test_by_time_save_keeps_the_time_labels(tmp_path: Path) -> None:
    target = tmp_path / "when.svg"
    run_chart(
        ChartRequest(by_time="ts:1h", save=target),
        stdin=io.StringIO('{"ts": "2025-01-01T14:05:00Z"}\n{"ts": "2025-01-01T16:10:00Z"}\n'),
        stdout=io.StringIO(),
    )
    svg = target.read_text(encoding="utf-8")
    assert "14:00" in svg and "16:00" in svg


def test_by_time_excludes_field_and_facet() -> None:
    import pytest

    from smartpipe.core.errors import UsageFault

    with pytest.raises(UsageFault, match="--by-time replaces"):
        run_chart(
            ChartRequest(field="x", by_time="ts:1h"),
            stdin=io.StringIO(""),
            stdout=io.StringIO(),
        )
