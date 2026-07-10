"""The run-manifest builder (item 65a): the citable methods-section record.

One JSON document per run - what ran (verb + argv), with which resolved
models and prompt (text AND sha256), over how many items, at what observed
cost, ending how. Pure: every fact arrives as a parameter (the shell in
``io/manifest.py`` gathers clocks, argv, and the meter); this module only
shapes and hashes.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Mapping

__all__ = ["MANIFEST_VERSION", "ItemCounts", "build_manifest", "prompt_sha256"]

MANIFEST_VERSION = 1


@dataclass(frozen=True, slots=True)
class ItemCounts:
    """End-of-run item accounting. smartpipe's runner turns every per-item
    failure into a skip-and-warn (never a crash), so ``skipped`` and
    ``failed`` name the same rows today - both ride the manifest so its
    schema stays stable if the taxonomy ever splits them."""

    succeeded: int
    skipped: int

    @property
    def total(self) -> int:
        return self.succeeded + self.skipped


def prompt_sha256(text: str) -> str:
    """The prompt's fingerprint - lets a reader verify the quoted text."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def build_manifest(
    *,
    version: str,
    verb: str,
    argv: tuple[str, ...],
    models: Mapping[str, str],
    prompt: str | None,
    schema: Mapping[str, object] | None,
    temperature: float,
    counts: ItemCounts | None,
    tokens_in: int,
    tokens_out: int,
    paid_conversions: int,
    started_at: str,
    finished_at: str,
    exit_code: int,
    exit_status: str,
) -> dict[str, object]:
    """The manifest document, ready for ``json.dumps``. ``None`` marks facts
    the run genuinely had no value for (no prompt, no schema, no accounting) -
    honest nulls, never invented zeros."""
    return {
        "manifest_version": MANIFEST_VERSION,
        "smartpipe_version": version,
        "verb": verb,
        "argv": list(argv),
        "models": dict(models),
        "prompt": None if prompt is None else {"text": prompt, "sha256": prompt_sha256(prompt)},
        "schema": None if schema is None else dict(schema),
        "determinism": {"temperature": temperature},
        "items": (
            None
            if counts is None
            else {
                "in": counts.total,
                "succeeded": counts.succeeded,
                "skipped": counts.skipped,
                "failed": counts.skipped,
            }
        ),
        "receipt": {
            "tokens_in": tokens_in,
            "tokens_out": tokens_out,
            "paid_conversions": paid_conversions,
        },
        "run": {
            "started_at": started_at,
            "finished_at": finished_at,
            "exit_code": exit_code,
            "exit_status": exit_status,
        },
    }
