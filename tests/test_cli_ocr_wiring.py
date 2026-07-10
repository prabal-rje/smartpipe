"""Item 48 residual: the ocr-model role in the remaining ingestion paths.

One pin per newly wired verb: with the role UNSET, feeding a scan makes ZERO
HTTP calls (``respx.mock`` with no routes turns any request into a loud
failure) and the behavior is byte-identical to before the wiring. Plus the
role-SET happy path through the real mistral wire for the free verbs.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest
import respx

from tests.conftest import RunCli

if TYPE_CHECKING:
    from pathlib import Path

_PNG = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64  # the magic is all detect_kind needs
_OCR_URL = "https://api.mistral.ai/v1/ocr"


@pytest.fixture(autouse=True)
def no_ambient_models(monkeypatch: pytest.MonkeyPatch) -> None:
    """The pins must not inherit the developer's model env."""
    for name in ("SMARTPIPE_MODEL", "SMARTPIPE_EMBED_MODEL", "SMARTPIPE_OCR_MODEL"):
        monkeypatch.delenv(name, raising=False)


def _ocr_env(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("SMARTPIPE_OCR_MODEL", "mistral/mistral-ocr-latest")
    monkeypatch.setenv("MISTRAL_API_KEY", "mk-test")


def _page(markdown: str) -> dict[str, object]:
    return {"index": 0, "markdown": markdown, "images": [], "tables": []}


# --- unset role = zero HTTP calls, byte-identical behavior (one pin per verb) ---------


def test_cluster_without_ocr_model_makes_zero_calls(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scan.png").write_bytes(_PNG)
    with respx.mock:  # no routes: ANY http call would fail the test
        code, out, err = run_cli(["cluster", "scan.png"], stdin="")
    assert code == 3  # today's behavior: the image can't caption, nothing embeds
    assert out == ""
    assert "excluded: scan.png" in err


def test_diff_without_ocr_model_makes_zero_calls(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    monkeypatch.chdir(tmp_path)
    monkeypatch.setenv("SMARTPIPE_EMBED_MODEL", "ollama/nomic-embed-text")
    (tmp_path / "before.log").write_text("alpha\n", encoding="utf-8")
    with respx.mock:  # only the embed wire is mocked — an OCR call would fail loudly
        respx.post("http://localhost:11434/api/embed").mock(
            return_value=httpx.Response(200, json={"embeddings": [[1.0, 0.0]]})
        )
        code, _out, _err = run_cli(["diff", "--right", "before.log"], stdin="alpha\n")
    assert code == 0


def test_distinct_without_ocr_model_makes_zero_calls(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scan.png").write_bytes(_PNG)
    with respx.mock:
        code, out, err = run_cli(["distinct", "scan.png"], stdin="")
    assert code == 0
    assert out == "\n"  # the image item's raw text is empty — kept unexamined, as today
    assert "kept unexamined: scan.png" in err


def test_outliers_without_ocr_model_makes_zero_calls(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    for name in ("a.png", "b.png", "c.png"):
        (tmp_path / name).write_bytes(_PNG)
    with respx.mock:
        code, _out, err = run_cli(["outliers", "3", "*.png"], stdin="")
    assert code == 64  # today's behavior: no scan embeds, so normal can't be learned
    assert "needs at least 3 embeddable items" in err


def test_split_without_ocr_model_makes_zero_calls(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scan.png").write_bytes(_PNG)
    with respx.mock:
        code, out, err = run_cli(["split", "scan.png"], stdin="")
    assert code == 3
    assert out == ""
    assert "image items need map" in err  # today's text-verbs skip, unchanged


def test_join_without_ocr_model_makes_zero_calls(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.chdir(tmp_path)
    (tmp_path / "scan.png").write_bytes(_PNG)
    (tmp_path / "right.jsonl").write_text('{"sku": "A1"}\n', encoding="utf-8")
    with respx.mock:
        # --in (not a positional): join's first positional is the predicate
        code, out, _err = run_cli(
            ["join", "--on", "left.sku == right.sku", "--right", "right.jsonl", "--in", "scan.png"],
            stdin="",
        )
    assert code == 0
    assert out == ""  # the scan has no fields, so no key, no match — as today


# --- role set: the free verbs spend only through the disclosed OCR wire ---------------


def test_split_routes_scans_through_the_configured_ocr_model(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    monkeypatch.chdir(tmp_path)
    _ocr_env(monkeypatch)
    (tmp_path / "scan.png").write_bytes(_PNG)
    with respx.mock:
        respx.post(_OCR_URL).mock(
            return_value=httpx.Response(200, json={"pages": [_page("SCANNED TEXT")]})
        )
        code, out, err = run_cli(["split", "scan.png"], stdin="")
    assert code == 0
    (row,) = [json.loads(line) for line in out.splitlines()]
    assert row["text"] == "SCANNED TEXT"
    assert "parsed by mistral/mistral-ocr-latest" in err


def test_split_max_calls_caps_the_ocr_spend(
    run_cli: RunCli, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    import httpx

    monkeypatch.chdir(tmp_path)
    _ocr_env(monkeypatch)
    (tmp_path / "a.png").write_bytes(_PNG)
    (tmp_path / "b.png").write_bytes(_PNG)
    with respx.mock:
        respx.post(_OCR_URL).mock(return_value=httpx.Response(200, json={"pages": [_page("MD")]}))
        code, out, err = run_cli(["split", "*.png", "--max-calls", "1"], stdin="")
    assert code == 1  # PARTIAL: the belt fired, completeness can't be trusted
    assert len(out.splitlines()) == 1  # intake stopped after the limit call
    assert "stopped by --max-calls (1 calls made)" in err
