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
from pathlib import Path
from typing import TYPE_CHECKING

from smartpipe.core.errors import UsageFault

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smartpipe.core.errors import ExitCode, SourceCounts

__all__ = [
    "abandon",
    "begin",
    "finish",
    "guard_manifest_alias",
    "guard_manifest_tree",
    "record_counts",
    "record_model",
    "record_schema",
    "replace_counts",
    "reset",
]

# CompletionRequest's pinned request temperature (D36: a pipe is a data tool,
# reproducible by default). tests/io/test_manifest.py asserts they agree.
_TEMPERATURE = 0.0


@dataclass(slots=True)
class _Collector:
    path: Path
    reserved_path: Path
    reserved_fd: int
    verb: str
    argv: tuple[str, ...]
    started_at: str
    prompt: str | None = None
    schema: Mapping[str, object] | None = None
    models: dict[str, str] = dataclass_field(default_factory=dict[str, str])
    counts: tuple[int, int, int] | None = None  # (succeeded, skipped, failed subset)


_state: _Collector | None = None


def reset() -> None:
    global _state
    held, _state = _state, None
    if held is not None:
        _discard_reservation(held)


def abandon() -> None:
    """Disarm an explicitly cancelled run that produced no result."""
    reset()


def begin(path: Path, *, verb: str, argv: tuple[str, ...], prompt: str | None = None) -> None:
    """Arm the collector. Faults on an unwritable destination NOW - before
    any model resolution or spend - so a typo'd path can't eat a paid run."""
    global _state
    reset()
    parent = path.parent
    if not parent.is_dir():
        raise UsageFault(
            f"--manifest: directory '{parent}' does not exist\n"
            "  Create it first - the manifest is written there at run end."
        )
    if path.is_dir():
        raise UsageFault(
            f"--manifest: target '{path}' is a directory\n"
            "  Choose a file path - the manifest is one JSON document."
        )
    started_at = _utc_now()
    reserved_fd, reserved_path = _reserve_destination(parent)
    _state = _Collector(
        path=path,
        reserved_path=reserved_path,
        reserved_fd=reserved_fd,
        verb=verb,
        argv=argv,
        started_at=started_at,
        prompt=prompt,
    )


def guard_manifest_alias(path: Path, *, role: str) -> None:
    """Refuse a run path that resolves to the armed manifest destination.

    ``samefile`` catches symlinks and hardlinks when both names exist; the
    canonical fallback catches equivalent spellings and output paths that do
    not exist yet. The reservation is abandoned before the usage fault so a
    rejected run leaves neither a manifest nor its held temporary file.
    """
    if _state is None or not _paths_alias(path, _state.path):
        return
    target = _state.path
    abandon()
    raise UsageFault(
        f"--manifest: target '{target}' aliases {role} '{path}'\n"
        "  Choose a different --manifest path - it must not replace a file used by this run."
    )


def guard_manifest_tree(directory: Path, *, role: str) -> None:
    """Refuse a manifest anywhere inside a directory-shaped output.

    Canonical paths catch spelling and symlink aliases. Existing hardlinks
    are caught by checking the directory's current files, so a vault writer
    cannot replace the armed manifest through another name.
    """
    if _state is None:
        return
    target = _state.path
    canonical_directory = directory.resolve(strict=False)
    canonical_target = target.resolve(strict=False)
    try:
        canonical_target.relative_to(canonical_directory)
    except ValueError:
        nested = False
    else:
        nested = True
    if not nested:
        hardlinked = directory.is_dir() and any(
            candidate.is_file() and _paths_alias(candidate, target)
            for candidate in directory.rglob("*")
        )
        if not hardlinked:
            return
    abandon()
    raise UsageFault(
        f"--manifest: target '{target}' is inside {role} '{directory}'\n"
        "  Choose a different --manifest path - an output tree must not replace its record."
    )


def record_model(role: str, ref: str) -> None:
    """One resolved model ref per role (chat/embed/media_embed/ocr/stt/…)."""
    if _state is not None:
        _state.models[role] = ref


def record_schema(schema: Mapping[str, object] | None) -> None:
    """The compiled schema the run actually enforced (braces, DSL, or file)."""
    if _state is not None and schema is not None:
        _state.schema = schema


def record_counts(
    *,
    done: int,
    skipped: int,
    failed: int = 0,
    input_count: int | None = None,
) -> None:
    """End-of-run item accounting - the last report wins (it IS the end)."""
    from smartpipe.engine.manifest import ItemCounts

    counts = ItemCounts(succeeded=done, skipped=skipped, failed=failed)
    if input_count is not None and input_count != counts.total:
        raise ValueError(
            f"input count {input_count} does not equal done + skipped ({counts.total})"
        )
    from smartpipe.core.errors import SourceCounts
    from smartpipe.io import source_accounting

    source_accounting.record_local(
        SourceCounts(
            succeeded=counts.succeeded,
            skipped=counts.skipped,
            failed=counts.failed,
        )
    )
    replace_counts(
        SourceCounts(
            succeeded=counts.succeeded,
            skipped=counts.skipped,
            failed=counts.failed,
        )
    )


def replace_counts(counts: SourceCounts) -> None:
    """Replace stored counts after the CLI settles run-scoped source drops."""
    if _state is not None:
        _state.counts = (counts.succeeded, counts.skipped, counts.failed)


def finish(code: ExitCode) -> None:
    """Write the manifest and disarm. Called only on exit paths that produced
    results; unarmed calls no-op. Results already shipped when this runs, so
    write trouble warns instead of masking the run's exit code."""
    global _state
    if _state is None:
        return
    held, _state = _state, None
    try:
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
            _write_reserved(held, document)
        except OSError as exc:
            diagnostics.warn(f"manifest: could not write {held.path} ({exc})")
            return
        diagnostics.note(f"manifest: {held.path}")
    finally:
        _discard_reservation(held)


def _utc_now() -> str:
    return datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")


def _reserve_destination(parent: Path) -> tuple[int, Path]:
    """Hold the same-directory atomic-write file before input or spend."""
    fd: int | None = None
    tmp: str | None = None
    try:
        fd, tmp = tempfile.mkstemp(dir=parent, suffix=".manifest.tmp")
        return fd, Path(tmp)
    except OSError as exc:
        import contextlib

        if fd is not None:
            with contextlib.suppress(OSError):
                os.close(fd)
        if tmp is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp)
        raise UsageFault(
            f"--manifest: directory '{parent}' is not writable ({exc})\n"
            "  Choose a writable directory before starting the run."
        ) from exc


def _write_reserved(held: _Collector, document: Mapping[str, object]) -> None:
    """Write and replace through the file reserved when the run began."""
    handle = os.fdopen(held.reserved_fd, "w", encoding="utf-8")
    held.reserved_fd = -1
    with handle:
        handle.write(json.dumps(document, ensure_ascii=False, indent=2) + "\n")
    os.replace(held.reserved_path, held.path)


def _discard_reservation(held: _Collector) -> None:
    import contextlib

    if held.reserved_fd >= 0:
        with contextlib.suppress(OSError):
            os.close(held.reserved_fd)
        held.reserved_fd = -1
    with contextlib.suppress(OSError):
        held.reserved_path.unlink()


def _paths_alias(left: Path, right: Path) -> bool:
    try:
        return left.samefile(right)
    except OSError:
        left_name = os.path.normcase(str(left.resolve(strict=False)))
        right_name = os.path.normcase(str(right.resolve(strict=False)))
        return left_name == right_name
