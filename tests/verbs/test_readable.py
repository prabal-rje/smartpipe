"""The readable verb (wave 2, item 25): the explicit human door."""

from __future__ import annotations

import io
import json

from smartpipe.core.errors import ExitCode
from smartpipe.verbs.readable import ReadableRequest, run_readable


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
