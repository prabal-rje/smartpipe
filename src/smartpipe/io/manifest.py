"""The ``--manifest`` sidecar (item 65a): run-scoped facts, one atomic write.

Module-level and run-scoped like ``io/metering`` - the documented exception
to no-globals (one verb per process; ``reset()`` at container build and in
tests). The container and verbs feed facts as they resolve; the CLI layer
arms the collector (``begin``, before any spend) and settles it (``finish``)
on every exit path that produced results. The file never touches stdout, and
a second run overwrites it - the manifest is a record of THIS run.
"""

from __future__ import annotations

import json
import os
import tempfile
from dataclasses import dataclass
from dataclasses import field as dataclass_field
from datetime import UTC, datetime
from typing import TYPE_CHECKING

from smartpipe.core.errors import UsageFault

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

    from smartpipe.core.errors import ExitCode

__all__ = [
    "begin",
    "finish",
    "record_counts",
    "record_model",
    "record_schema",
    "reset",
]

# CompletionRequest's pinned request temperature (D36: a pipe is a data tool,
# reproducible by default). tests/io/test_manifest.py asserts they agree.
_TEMPERATURE = 0.0


@dataclass(slots=True)
class _Collector:
    path: Path
    verb: str
    argv: tuple[str, ...]
    started_at: str
    prompt: str | None = None
    schema: Mapping[str, object] | None = None
    models: dict[str, str] = dataclass_field(default_factory=dict[str, str])
    counts: tuple[int, int] | None = None  # (succeeded, skipped)


_state: _Collector | None = None


def reset() -> None:
    global _state
    _state = None


def begin(path: Path, *, verb: str, argv: tuple[str, ...], prompt: str | None = None) -> None:
    """Arm the collector. Faults on an unwritable destination NOW - before
    any model resolution or spend - so a typo'd path can't eat a paid run."""
    global _state
    parent = path.parent
    if not parent.is_dir():
        raise UsageFault(
            f"--manifest: directory '{parent}' does not exist\n"
            "  Create it first - the manifest is written there at run end."
        )
    _state = _Collector(path=path, verb=verb, argv=argv, started_at=_utc_now(), prompt=prompt)


def record_model(role: str, ref: str) -> None:
    """One resolved model ref per role (chat/embed/media_embed/ocr/stt/…)."""
    if _state is not None:
        _state.models[role] = ref


def record_schema(schema: Mapping[str, object] | None) -> None:
    """The compiled schema the run actually enforced (braces, DSL, or file)."""
    if _state is not None and schema is not None:
        _state.schema = schema


def record_counts(*, done: int, skipped: int) -> None:
    """End-of-run item accounting - the last report wins (it IS the end)."""
    if _state is not None:
        _state.counts = (done, skipped)


def finish(code: ExitCode) -> None:
    """Write the manifest and disarm. Called only on exit paths that produced
    results; unarmed calls no-op. Results already shipped when this runs, so
    write trouble warns instead of masking the run's exit code."""
    global _state
    if _state is None:
        return
    held, _state = _state, None
    from smartpipe import __version__
    from smartpipe.engine.manifest import ItemCounts, build_manifest
    from smartpipe.io import diagnostics, metering

    view = metering.snapshot()
    document = build_manifest(
        version=__version__,
        verb=held.verb,
        argv=held.argv,
        models=held.models,
        prompt=held.prompt,
        schema=held.schema,
        temperature=_TEMPERATURE,
        counts=None if held.counts is None else ItemCounts(*held.counts),
        tokens_in=view.tokens_in,
        tokens_out=view.tokens_out,
        paid_conversions=view.conversions,
        started_at=held.started_at,
        finished_at=_utc_now(),
        exit_code=int(code),
        exit_status=code.name.lower(),
    )
    try:
        _write_atomic(held.path, document)
    except OSError as exc:
        diagnostics.warn(f"manifest: could not write {held.path} ({exc})")
        return
    diagnostics.note(f"manifest: {held.path}")


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _write_atomic(path: Path, document: Mapping[str, object]) -> None:
    """Same-directory temp file + ``os.replace`` - the config-store pattern:
    a concurrent reader can never see a torn manifest."""
    fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
        os.replace(tmp, path)
    except BaseException:
        import contextlib

        with contextlib.suppress(OSError):
            os.unlink(tmp)
        raise
