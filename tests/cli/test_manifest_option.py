"""The manifest settling wrapper: every results-producing exit path writes."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

import pytest

from smartpipe.cli.manifest_option import settled
from smartpipe.core.errors import ExitCode, TooManyFailures
from smartpipe.io import manifest

if TYPE_CHECKING:
    from pathlib import Path


async def test_the_majority_failure_halt_still_writes_the_manifest(tmp_path: Path) -> None:
    # results streamed before the halt tripped - the record must land, with
    # the halt's own accounting and the all_failed exit it maps to
    manifest.reset()
    target = tmp_path / "run.json"
    manifest.begin(target, verb="map", argv=("map", "x"))

    async def exploding() -> ExitCode:
        raise TooManyFailures(12, 20, "the same way")

    with pytest.raises(TooManyFailures):
        await settled(exploding(), None)
    document = json.loads(target.read_text(encoding="utf-8"))
    assert document["items"] == {"in": 20, "succeeded": 8, "skipped": 12, "failed": 12}
    assert document["run"]["exit_status"] == "all_failed"
