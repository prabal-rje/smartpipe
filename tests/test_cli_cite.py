"""``sempipe cite`` — the BibTeX block is a golden-pinned contract."""

from __future__ import annotations

from pathlib import Path

from tests.conftest import RunCli

GOLDEN = Path(__file__).parent / "golden" / "cite.bibtex"


def test_cite_prints_the_bibtex_block(run_cli: RunCli) -> None:
    code, out, err = run_cli(["cite"])
    assert code == 0
    assert err == ""
    assert out == GOLDEN.read_text(encoding="utf-8")


def test_cite_carries_the_current_version(run_cli: RunCli) -> None:
    from sempipe import __version__

    _code, out, _err = run_cli(["cite"])
    assert f"version = {{{__version__}}}" in out
