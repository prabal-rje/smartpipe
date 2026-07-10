"""The playground corpus: one pinned release asset, fetched, verified, unpacked.

``smartpipe demo`` rests on three facts that must never drift apart: the asset
URL the docs advertise, the sha256 GitHub publishes for that asset, and the
layout the tarball unpacks to. They live here as constants, next to the code
that acts on them - the streaming download (httpx, function-local so the
startup budget holds), the digest gate, and the staged unpack (safe-filtered,
extracted beside the target and published by one rename, so an interrupted run
leaves nothing behind). The decision flow that calls all of this lives in
``cli/demo_cmd`` with these effects injected.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import TYPE_CHECKING

from smartpipe.core.errors import SetupFault

if TYPE_CHECKING:
    from collections.abc import Iterable

__all__ = [
    "EXPECTED_ENTRIES",
    "PLAYGROUND_DIR",
    "PLAYGROUND_SHA256",
    "PLAYGROUND_SIZE_LABEL",
    "PLAYGROUND_URL",
    "fetch_corpus",
    "looks_complete",
    "unpack",
    "verify",
]

# The v1 release asset every doc page points at (26,079,883 bytes; the sha256
# is the one GitHub publishes in the release metadata). A revised corpus ships
# as v2 with new pins - never as a mutated v1, which this digest would refuse.
PLAYGROUND_URL = (
    "https://github.com/prabal-rje/smartpipe-playground/releases/download/v1/"
    "smartpipe-playground-v1.tar.gz"
)
PLAYGROUND_SHA256 = "1a84a050d7eb270bd6cc620e817c1757bd0349a6b12df8fbddf755df34f8ba5b"
PLAYGROUND_SIZE_LABEL = "~26 MB"
PLAYGROUND_DIR = "smartpipe-playground"

# The corpus's six content directories - the completeness witness for the
# already-here check (README.md and LICENSES.md ride along, not load-bearing).
EXPECTED_ENTRIES = frozenset(("data", "invoices", "photos", "recordings", "reports", "sessions"))

_TIMEOUT_SECONDS = 120.0


def looks_complete(entries: Iterable[str]) -> bool:
    """A directory listing that holds every content dir is a prior download."""
    return set(entries) >= EXPECTED_ENTRIES


def fetch_corpus(url: str, *, timeout: float = _TIMEOUT_SECONDS) -> bytes:
    """Stream the release asset into memory (26 MB - well within reach).

    Any wire failure is a ``SetupFault``: the environment is what's broken,
    and the screen carries the by-hand fallback.
    """
    import httpx

    from smartpipe.cli.screens import demo_download_failed

    try:
        with (
            httpx.Client(follow_redirects=True, timeout=timeout) as client,
            client.stream("GET", url) as response,
        ):
            response.raise_for_status()
            return b"".join(response.iter_bytes())
    except httpx.HTTPError as exc:
        raise SetupFault(demo_download_failed(url, str(exc))) from exc


def verify(data: bytes, *, expected_sha256: str) -> None:
    """The digest gate between the wire and the disk - nothing unpacks unverified."""
    import hashlib

    from smartpipe.cli.screens import demo_verify_failed

    actual = hashlib.sha256(data).hexdigest()
    if actual != expected_sha256:
        raise SetupFault(demo_verify_failed(expected_sha256, actual))


def unpack(data: bytes, target: Path) -> None:
    """Extract the tarball's ``smartpipe-playground/`` to ``target``.

    Staged: extract into a temp dir beside the target (same filesystem), then
    one rename publishes the corpus - a failure mid-extract leaves no
    half-corpus at the target. The ``data`` filter refuses traversal and
    device/link tricks: the digest vouched for the bytes, this vouches for
    the shape.
    """
    import io
    import tarfile
    import tempfile

    target.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(dir=target.parent, prefix=".smartpipe-demo-") as staging:
        with tarfile.open(fileobj=io.BytesIO(data), mode="r:gz") as archive:
            archive.extractall(staging, filter="data")
        unpacked = Path(staging) / PLAYGROUND_DIR
        if not unpacked.is_dir() or not looks_complete(entry.name for entry in unpacked.iterdir()):
            raise SetupFault(
                "error: the playground tarball layout is unexpected - nothing was unpacked\n"
                f"  The archive should hold {PLAYGROUND_DIR}/ with its six content folders.\n"
                "  This is a bug in the published asset, not in your usage - please report it:\n"
                "  https://github.com/prabal-rje/smartpipe/issues"
            )
        if target.is_dir():
            target.rmdir()  # only ever empty here - run_demo refused anything non-empty
        os.replace(unpacked, target)
