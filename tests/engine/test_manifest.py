"""The manifest builder (item 65a): pure shaping + hashing of run facts."""

from __future__ import annotations

import hashlib

from smartpipe.engine.manifest import MANIFEST_VERSION, ItemCounts, build_manifest, prompt_sha256


def _build(**overrides: object) -> dict[str, object]:
    base: dict[str, object] = {
        "version": "1.4.0",
        "verb": "map",
        "argv": ("map", "Extract {label}", "--manifest", "run.json"),
        "models": {"chat": "ollama/qwen3:8b"},
        "prompt": "Extract {label}",
        "schema": {"type": "object"},
        "temperature": 0.0,
        "counts": ItemCounts(succeeded=9, skipped=1),
        "tokens_in": 1200,
        "tokens_out": 340,
        "paid_conversions": 2,
        "started_at": "2026-07-10T12:00:00Z",
        "finished_at": "2026-07-10T12:00:05Z",
        "exit_code": 1,
        "exit_status": "partial",
    }
    base.update(overrides)
    return build_manifest(**base)  # type: ignore[arg-type]


def test_shape_of_a_full_run() -> None:
    document = _build()
    assert document["manifest_version"] == MANIFEST_VERSION
    assert document["smartpipe_version"] == "1.4.0"
    assert document["verb"] == "map"
    assert document["argv"] == ["map", "Extract {label}", "--manifest", "run.json"]
    assert document["models"] == {"chat": "ollama/qwen3:8b"}
    assert document["schema"] == {"type": "object"}
    assert document["determinism"] == {"temperature": 0.0}
    assert document["receipt"] == {"tokens_in": 1200, "tokens_out": 340, "paid_conversions": 2}
    assert document["run"] == {
        "started_at": "2026-07-10T12:00:00Z",
        "finished_at": "2026-07-10T12:00:05Z",
        "exit_code": 1,
        "exit_status": "partial",
    }


def test_prompt_carries_text_and_its_sha256() -> None:
    document = _build()
    expected = hashlib.sha256(b"Extract {label}").hexdigest()
    assert document["prompt"] == {"text": "Extract {label}", "sha256": expected}
    assert prompt_sha256("Extract {label}") == expected


def test_no_prompt_stays_null() -> None:
    assert _build(prompt=None)["prompt"] is None


def test_no_schema_stays_null() -> None:
    assert _build(schema=None)["schema"] is None


def test_counts_record_failed_alongside_skipped() -> None:
    # smartpipe's runner turns every per-item failure into a skip-and-warn,
    # so `skipped` and `failed` name the same rows today; both are emitted
    # so the schema stays stable if the taxonomy ever splits them.
    document = _build(counts=ItemCounts(succeeded=7, skipped=3))
    assert document["items"] == {"in": 10, "succeeded": 7, "skipped": 3, "failed": 3}


def test_unreported_counts_stay_null() -> None:
    assert _build(counts=None)["items"] is None
