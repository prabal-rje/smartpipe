"""The PEP 440-lite comparator behind the update notice.

The one behavior that matters: never nag someone already running something
newer than the latest stable — including release candidates of the NEXT
version — and always nag when the stable release of their rc lands.
"""

from __future__ import annotations

import pytest

from smartpipe.core.versions import is_newer, parse_version

# --- parse_version -----------------------------------------------------------


def test_parse_plain_release() -> None:
    parsed = parse_version("1.4.0")
    assert parsed is not None
    assert parsed.release == (1, 4, 0)
    assert parsed.pre is None
    assert parsed.post is None
    assert parsed.dev is None


def test_parse_release_candidate() -> None:
    parsed = parse_version("1.4.0rc1")
    assert parsed is not None
    assert parsed.release == (1, 4, 0)
    assert parsed.pre == (2, 1)  # rc ranks above a (0) and b (1)


@pytest.mark.parametrize(
    ("text", "phase"),
    [("2.0a1", 0), ("2.0alpha1", 0), ("2.0b3", 1), ("2.0beta3", 1), ("2.0rc2", 2), ("2.0c2", 2)],
)
def test_parse_pre_release_aliases(text: str, phase: int) -> None:
    parsed = parse_version(text)
    assert parsed is not None and parsed.pre is not None
    assert parsed.pre[0] == phase


def test_parse_post_and_dev() -> None:
    parsed = parse_version("1.2.post3")
    assert parsed is not None and parsed.post == 3
    parsed = parse_version("1.2.dev4")
    assert parsed is not None and parsed.dev == 4


def test_parse_tolerates_v_prefix_separators_and_local_suffix() -> None:
    assert parse_version("v1.4.0") == parse_version("1.4.0")
    assert parse_version("1.4.0-rc.1") == parse_version("1.4.0rc1")
    assert parse_version("1.4.0+local.7") == parse_version("1.4.0")


@pytest.mark.parametrize("text", ["", "not-a-version", "1.x.0", "1..2", "1.0rc", "1!2.0"])
def test_parse_rejects_junk(text: str) -> None:
    assert parse_version(text) is None


# --- is_newer ----------------------------------------------------------------


@pytest.mark.parametrize(
    ("candidate", "current"),
    [
        ("1.4.0", "1.3.1"),
        ("1.4.0", "1.4.0rc1"),  # the stable release of your rc SHOULD nag
        ("1.4.0rc2", "1.4.0rc1"),
        ("1.4.1", "1.4.0"),
        ("1.10.0", "1.9.9"),  # numeric, not lexicographic
        ("1.4.0", "1.4.0.dev1"),
        ("1.4.0.post1", "1.4.0"),
        ("2.0.0b1", "2.0.0a2"),
    ],
)
def test_is_newer_true(candidate: str, current: str) -> None:
    assert is_newer(candidate, current)


@pytest.mark.parametrize(
    ("candidate", "current"),
    [
        ("1.3.1", "1.4.0rc1"),  # running an rc newer than latest stable: NO nag
        ("1.4.0", "1.4.0"),
        ("1.4.0", "1.4"),  # trailing zeros are the same version
        ("1.4.0rc1", "1.4.0"),
        ("1.3.9", "1.4.0"),
        ("1.4.0.dev1", "1.4.0rc1"),
    ],
)
def test_is_newer_false(candidate: str, current: str) -> None:
    assert not is_newer(candidate, current)


def test_is_newer_unparseable_never_warns() -> None:
    assert not is_newer("garbage", "1.4.0")
    assert not is_newer("1.5.0", "garbage")
    assert not is_newer("garbage", "garbage")
