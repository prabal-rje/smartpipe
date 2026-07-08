"""PEP 440-lite version comparison for the update notice — pure, no I/O.

Hand-rolled on purpose: `packaging` is only a *transitive* dependency today
(via matplotlib), and the update check must never be the reason a dependency
sticks around. The subset understood here — release digits, a/b/rc
pre-releases, `.postN`, `.devN`, an ignored local `+suffix` — covers every
version PyPI's normalized `info.version` can return for smartpipe-cli.
Anything else parses to ``None`` and never triggers a notice: when in doubt,
stay quiet.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Version", "is_newer", "parse_version"]

_PHASES = {"a": 0, "alpha": 0, "b": 1, "beta": 1, "c": 2, "rc": 2}

_VERSION_RE = re.compile(
    r"""^v?
        (?P<release>\d+(?:\.\d+)*)
        (?:[-._]?(?P<phase>a|alpha|b|beta|c|rc)[-._]?(?P<pre_n>\d+))?
        (?:[-._]?post[-._]?(?P<post>\d+))?
        (?:[-._]?dev[-._]?(?P<dev>\d+))?
        (?:\+[A-Za-z0-9.]+)?
        $""",
    re.VERBOSE | re.IGNORECASE,
)


@dataclass(frozen=True, slots=True)
class Version:
    release: tuple[int, ...]
    pre: tuple[int, int] | None  # (phase rank: a=0 b=1 rc=2, number)
    post: int | None
    dev: int | None


def parse_version(text: str) -> Version | None:
    matched = _VERSION_RE.match(text.strip())
    if matched is None:
        return None
    pre: tuple[int, int] | None = None
    if matched.group("phase") is not None:
        pre = (_PHASES[matched.group("phase").lower()], int(matched.group("pre_n")))
    post = matched.group("post")
    dev = matched.group("dev")
    return Version(
        release=tuple(int(part) for part in matched.group("release").split(".")),
        pre=pre,
        post=None if post is None else int(post),
        dev=None if dev is None else int(dev),
    )


def is_newer(candidate: str, current: str) -> bool:
    """True only when both parse and ``candidate`` orders strictly after
    ``current`` under PEP 440 (dev < a < b < rc < final < post)."""
    parsed_candidate = parse_version(candidate)
    parsed_current = parse_version(current)
    if parsed_candidate is None or parsed_current is None:
        return False
    return _sort_key(parsed_candidate) > _sort_key(parsed_current)


def _sort_key(version: Version) -> tuple[tuple[int, ...], tuple[int, int], int, tuple[int, int]]:
    # Ranks mirror packaging's: a bare .devN sits below every pre-release,
    # a final release above every pre-release, .postN above the final.
    if version.pre is not None:
        pre_rank = version.pre
    elif version.dev is not None and version.post is None:
        pre_rank = (-1, 0)
    else:
        pre_rank = (3, 0)
    post_rank = -1 if version.post is None else version.post
    dev_rank = (1, 0) if version.dev is None else (0, version.dev)
    return (_stripped(version.release), pre_rank, post_rank, dev_rank)


def _stripped(release: tuple[int, ...]) -> tuple[int, ...]:
    """PEP 440: 1.4 and 1.4.0 are the same version — trailing zeros drop."""
    length = len(release)
    while length > 1 and release[length - 1] == 0:
        length -= 1
    return release[:length]
