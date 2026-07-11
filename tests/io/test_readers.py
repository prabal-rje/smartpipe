from __future__ import annotations

import io

import pytest

from smartpipe.core.errors import UsageFault
from smartpipe.io.items import Item
from smartpipe.io.readers import ensure_not_a_tty, stdin_items


async def _collect(stdin: io.StringIO) -> list[Item]:
    return [item async for item in stdin_items(stdin)]


async def test_yields_items_in_order_with_contiguous_indexes() -> None:
    items = await _collect(io.StringIO("a\nb\nc\n"))
    assert [item.raw for item in items] == ["a", "b", "c"]
    assert [item.source.index for item in items] == [0, 1, 2]


async def test_final_line_without_newline_is_an_item() -> None:
    items = await _collect(io.StringIO("a\nb"))
    assert [item.raw for item in items] == ["a", "b"]


async def test_empty_stdin_yields_nothing() -> None:
    assert await _collect(io.StringIO("")) == []


async def test_crlf_input() -> None:
    items = await _collect(io.StringIO("a\r\nb\r\n"))
    assert [item.raw for item in items] == ["a", "b"]


async def test_empty_lines_are_items() -> None:
    items = await _collect(io.StringIO("a\n\nb\n"))
    assert [item.raw for item in items] == ["a", "", "b"]


class _FakeTty(io.StringIO):
    def isatty(self) -> bool:
        return True


def test_tty_stdin_is_a_usage_fault() -> None:
    with pytest.raises(UsageFault) as excinfo:
        ensure_not_a_tty(_FakeTty())
    assert "pipe some input" in str(excinfo.value)


def test_piped_stdin_passes_the_guard() -> None:
    ensure_not_a_tty(io.StringIO("data\n"))  # must not raise


# --- scan routing disclosure (D39/03) ----------------------------------------------


def test_thin_text_with_figures_reads_as_a_scan() -> None:
    from smartpipe.io.readers import figure_note

    note = figure_note("contract.pdf", 11, 8, 22)
    assert "thin text layer (11 chars)" in note
    assert "scanned?" in note
    assert "split --by pages --media" in note  # the actionable past-the-cap hint


def test_real_text_keeps_the_plainfigure_note() -> None:
    from smartpipe.io.readers import figure_note

    note = figure_note("report.pdf", 5_000, 3, 0)
    assert note == "report.pdf: 3 figures attached"
    assert "scanned" not in note


async def test_figure_census_rolls_up_a_large_run(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B4: one ``note:`` per figure-bearing file drowns a big corpus. The first
    few announce verbatim (a small run is unchanged), then a single rollup closes
    the run instead of 50 near-identical lines."""
    from pathlib import Path

    from smartpipe.io import readers
    from smartpipe.models.base import ImageData
    from smartpipe.parsing import extract as extract_mod
    from smartpipe.parsing.extract import EmbeddedImage, EmbeddedMedia, Extracted

    assert isinstance(tmp_path, Path)
    for i in range(50):
        (tmp_path / f"doc{i:02d}.pdf").write_bytes(b"%PDF-1.4 tiny")

    def fake_extract(path: object, kind: object) -> Extracted:
        return Extracted(text="a genuine text layer " * 5)  # >64 chars: the plain branch

    def fake_embedded(path: object) -> EmbeddedMedia:
        img = ImageData(data=b"\x89PNGpayload", mime="image/png")
        return EmbeddedMedia(images=(EmbeddedImage(image=img, where="p.1 img.1"),), dropped_small=0)

    monkeypatch.setattr(readers, "extract", fake_extract)
    monkeypatch.setattr(extract_mod, "embedded_images", fake_embedded)

    items = readers.file_items(sorted(tmp_path.glob("*.pdf")))
    assert len(items) == 50 and all(item.media for item in items)  # every figure still attached
    err = capsys.readouterr().err
    verbatim = [line for line in err.splitlines() if line.endswith("figure attached")]
    assert len(verbatim) == 5  # _FIGURE_NOTE_CAP: first N verbatim, then suppressed
    assert err.count("more figure notes follow") == 1  # exactly one suppression line
    assert "note: figures attached: 50 files · 50 figures" in err  # the single rollup


async def test_figure_census_small_run_is_unchanged(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """A handful of files still print one verbatim note apiece and NO rollup."""
    from pathlib import Path

    from smartpipe.io import readers
    from smartpipe.models.base import ImageData
    from smartpipe.parsing import extract as extract_mod
    from smartpipe.parsing.extract import EmbeddedImage, EmbeddedMedia, Extracted

    assert isinstance(tmp_path, Path)
    for i in range(3):
        (tmp_path / f"doc{i}.pdf").write_bytes(b"%PDF-1.4 tiny")

    def fake_extract(path: object, kind: object) -> Extracted:
        return Extracted(text="a genuine text layer " * 5)

    def fake_embedded(path: object) -> EmbeddedMedia:
        img = ImageData(data=b"\x89PNGx", mime="image/png")
        return EmbeddedMedia(images=(EmbeddedImage(image=img, where="p.1 img.1"),), dropped_small=0)

    monkeypatch.setattr(readers, "extract", fake_extract)
    monkeypatch.setattr(extract_mod, "embedded_images", fake_embedded)
    readers.file_items(sorted(tmp_path.glob("*.pdf")))
    err = capsys.readouterr().err
    assert err.count("figure attached") == 3  # one verbatim note per file
    assert "figures attached:" not in err  # no rollup for a small run


async def test_figure_census_flushes_the_rollup_on_an_interrupted_read(
    tmp_path: object, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    """B4 review: files past the cap defer their figure note to the rollup, so a read
    abandoned mid-stream (a Ctrl-C, a downstream stop) must still flush it. The plain
    ``census.finish()`` after the loop used to be skipped when the generator was closed
    early, silencing every suppressed file's note; the try/finally now flushes it."""
    import io
    from collections.abc import AsyncGenerator
    from pathlib import Path

    from smartpipe.io import readers
    from smartpipe.models.base import ImageData
    from smartpipe.parsing import extract as extract_mod
    from smartpipe.parsing.extract import EmbeddedImage, EmbeddedMedia, Extracted

    assert isinstance(tmp_path, Path)
    for i in range(50):
        (tmp_path / f"doc{i:02d}.pdf").write_bytes(b"%PDF-1.4 tiny")

    def fake_extract(path: object, kind: object) -> Extracted:
        return Extracted(text="a genuine text layer " * 5)  # >64 chars: the plain branch

    def fake_embedded(path: object) -> EmbeddedMedia:
        img = ImageData(data=b"\x89PNGx", mime="image/png")
        return EmbeddedMedia(images=(EmbeddedImage(image=img, where="p.1 img.1"),), dropped_small=0)

    monkeypatch.setattr(readers, "extract", fake_extract)
    monkeypatch.setattr(extract_mod, "embedded_images", fake_embedded)

    names = "\n".join(str(tmp_path / f"doc{i:02d}.pdf") for i in range(50))
    gen = readers.from_files_items(io.StringIO(names))
    assert isinstance(gen, AsyncGenerator)  # narrow to the closable generator for aclose
    for _ in range(6):  # pull past the 5-note cap: files 6+ suppressed, the rollup pending
        await gen.__anext__()
    await gen.aclose()  # abandon the stream before EOF — a Ctrl-C / downstream stop
    err = capsys.readouterr().err
    assert "note: figures attached: 6 files · 6 figures" in err  # the rollup survived the abandon


# --- the kind census (wave 2, item 20) ---------------------------------------------


async def test_mixed_stream_notes_the_census_once(
    capsys: pytest.CaptureFixture[str],
) -> None:
    import io as _io

    from smartpipe.io.readers import stdin_items

    stream = _io.StringIO('{"a": 1}\nplain\n{"b": 2}\n')
    _ = [item async for item in stdin_items(stream)]
    err = capsys.readouterr().err
    assert "input: 2 records · 1 plain lines" in err


async def test_pure_streams_stay_silent(capsys: pytest.CaptureFixture[str]) -> None:
    import io as _io

    from smartpipe.io.readers import stdin_items

    stream = _io.StringIO('{"a": 1}\n{"b": 2}\n')
    _ = [item async for item in stdin_items(stream)]
    assert "input:" not in capsys.readouterr().err


async def test_strict_rows_raises_early_naming_the_mixed_row() -> None:
    import io as _io

    import pytest as _pytest

    from smartpipe.core.errors import UsageFault
    from smartpipe.io.readers import stdin_items

    stream = _io.StringIO('{"a": 1}\nplain\n{"b": 2}\n')
    collected: list[Item] = []
    with _pytest.raises(UsageFault) as excinfo:
        async for item in stdin_items(stream, strict_rows=True):
            collected.append(item)
    message = str(excinfo.value)
    assert "line 2 is a plain text line in a record stream" in message
    assert "--strict-rows demands one kind" in message
    # early: the offending row never reaches the verb, nor does anything after it
    assert [item.raw for item in collected] == ['{"a": 1}']


async def test_strict_rows_names_a_record_in_a_plain_stream() -> None:
    import io as _io

    import pytest as _pytest

    from smartpipe.core.errors import UsageFault
    from smartpipe.io.readers import stdin_items

    stream = _io.StringIO('plain\n{"a": 1}\n')
    with _pytest.raises(UsageFault, match="line 2 is a record in a plain-text stream"):
        _ = [item async for item in stdin_items(stream, strict_rows=True)]


async def test_strict_rows_env_var_matches_the_flag(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    import io as _io

    import pytest as _pytest

    from smartpipe.core.errors import UsageFault
    from smartpipe.io.readers import stdin_items

    monkeypatch.setenv("SMARTPIPE_STRICT_ROWS", "1")
    stream = _io.StringIO('{"a": 1}\nplain\n')
    with _pytest.raises(UsageFault, match="line 2 is a plain text line"):
        _ = [item async for item in stdin_items(stream)]
