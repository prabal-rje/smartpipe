from __future__ import annotations

import sempipe


def test_version_is_semver() -> None:
    major, minor, patch = sempipe.__version__.split(".")
    assert all(part.isdigit() for part in (major, minor, patch))
