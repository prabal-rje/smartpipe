"""The chart verb: counts in, bars out, SVG on request. Free."""

from __future__ import annotations

import io
from typing import TYPE_CHECKING

from sempipe.core.errors import ExitCode
from sempipe.engine.chart import render_bars, render_svg
from sempipe.verbs.chart import ChartRequest, run_chart

if TYPE_CHECKING:
    from pathlib import Path

NDJSON = (
    '{"label": "bug"}\n{"label": "bug"}\n{"label": "feature"}\n{"label": "bug"}\n{"other": 1}\n'
)


def test_field_tally_renders_ranked_bars() -> None:
    out = io.StringIO()
    code = run_chart(ChartRequest(field="label"), stdin=io.StringIO(NDJSON), stdout=out)
    assert code is ExitCode.OK
    lines = out.getvalue().splitlines()
    assert lines[0].startswith("bug")
    assert lines[0].rstrip().endswith("3")
    assert "▇" in lines[0]
    assert len(lines[0].split("▇")) > len(lines[1].split("▇"))  # widest bar wins
    assert any(line.startswith("(missing)") for line in lines)  # honest about gaps


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
    assert svg.startswith("<svg")
    assert "Ticket labels" in svg
    assert svg.count("<rect") >= 3  # background + one bar per value
    assert "bug" in svg and "3" in svg


def test_svg_escapes_labels() -> None:
    svg = render_svg([("<script>", 2)])
    assert "<script>" not in svg  # svgwrite escapes markup in text nodes


def test_bars_render_empty_input_honestly() -> None:
    assert render_bars([]) == "(nothing to chart)"
