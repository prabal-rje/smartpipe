"""The packaging metadata is a public contract: license, notice, citation."""

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).parent.parent


def test_license_is_apache() -> None:
    pyproject = (ROOT / "pyproject.toml").read_text(encoding="utf-8")
    assert 'license = "Apache-2.0"' in pyproject
    assert 'license-files = ["LICENSE", "NOTICE"]' in pyproject
    assert "Apache License" in (ROOT / "LICENSE").read_text(encoding="utf-8")


def test_notice_carries_attribution() -> None:
    notice = (ROOT / "NOTICE").read_text(encoding="utf-8")
    assert notice.startswith("sempipe")
    assert "Copyright 2026 Prabal Gupta" in notice


def test_citation_file_exists_and_names_the_author() -> None:
    citation = (ROOT / "CITATION.cff").read_text(encoding="utf-8")
    assert "cff-version:" in citation
    assert "Gupta" in citation
    assert "Apache-2.0" in citation
