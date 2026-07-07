from __future__ import annotations

import smartpipe


def test_version_is_semver() -> None:
    major, minor, patch = smartpipe.__version__.split(".")
    assert all(part.isdigit() for part in (major, minor, patch))
