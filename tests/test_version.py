from __future__ import annotations

import smartpipe


def test_version_is_semver() -> None:
    # PEP 440: X.Y.Z with an optional rcN tail (release candidates are real)
    import re

    assert re.fullmatch(r"\d+\.\d+\.\d+(rc\d+)?", smartpipe.__version__)
