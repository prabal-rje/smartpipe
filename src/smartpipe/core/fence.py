"""The ``--local-only`` data fence (item 65d): execution stays local.

One predicate (``local_only``), one honesty check (``is_local_host``), one
enforcement point (``ensure_local_wire``) - called by the composition root at
model-resolution time, BEFORE any spend or user-data upload. This is not an
air-gap switch: supporting requests that carry no user input, such as local
model-weight downloads, are allowed. Some side channels remain conservatively
quiet while the fence is armed, but zero network traffic is not the promise.

Fail-closed parsing: any value of ``SMARTPIPE_LOCAL_ONLY`` that is not an
explicit off-word arms the fence - a typo like ``LOCAL_ONLY=ture`` must fence,
never silently allow.

Local wires the fence admits: ollama on a loopback host (a remote
``OLLAMA_HOST`` is refused - sending items there IS data leaving), the
on-device fastembed embedder (``local/``), local whisper, the local
extraction ladder, and GLiNER - none of which route through here, because
they never open a socket to anyone else's machine.
"""

from __future__ import annotations

from ipaddress import IPv6Address, ip_address
from typing import TYPE_CHECKING
from urllib.parse import urlsplit

from smartpipe.core.errors import SetupFault

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smartpipe.models.base import ModelRef

__all__ = ["ensure_local_wire", "is_local_host", "local_only"]

_OFF_WORDS = ("", "0", "false", "off", "no")

_LOOPBACK_NAMES = frozenset({"localhost"})
_LOCAL_CLIENT_TARGETS = frozenset({"0.0.0.0"})  # accepted client target, not a bind claim


def local_only(env: Mapping[str, str]) -> bool:
    """Is the fence armed? ``SMARTPIPE_LOCAL_ONLY`` (the ``--local-only`` flag
    exports it) - explicit off-words disarm, anything else arms."""
    return env.get("SMARTPIPE_LOCAL_ONLY", "").strip().lower() not in _OFF_WORDS


def is_local_host(url: str) -> bool:
    """Does this URL point at THIS machine? Loopback names/addresses only."""
    target = url if "://" in url else f"http://{url}"
    try:
        host = (urlsplit(target).hostname or "").lower()
    except ValueError:
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        address = ip_address(host)
    except ValueError:
        return False
    if address.is_loopback or str(address) in _LOCAL_CLIENT_TARGETS:
        return True
    return (
        isinstance(address, IPv6Address)
        and address.ipv4_mapped is not None
        and address.ipv4_mapped.is_loopback
    )


_ROLE_WORDS = {
    "chat": "chat",
    "embed": "embedding",
    "media_embed": "media-embedding",
    "ocr": "document-parsing",
    "stt": "transcription",
}

_ROLE_FIXES = {
    "chat": "Local chat runs on ollama: smartpipe use ollama   (install: https://ollama.com)",
    "embed": "Local embeddings are built in: unset embed-model (the on-device embedder takes over)",
    "media_embed": "No local joint text+image space exists yet - unset media-embed-model "
    "(media then converts on-device)",
    "ocr": "Local extraction reads documents on-device: unset ocr-model",
    "stt": 'Local whisper transcribes on-device: stt-model = "local" (or unset it)',
}


def ensure_local_wire(
    ref: ModelRef, env: Mapping[str, str], *, role: str, ollama_host: str
) -> None:
    """Refuse a wire that would leave the machine - at resolution time,
    before any spend. ``ollama_host`` is the caller's resolved OLLAMA_HOST."""
    if not local_only(env):
        return
    if ref.provider == "local":
        return
    if ref.provider == "ollama":
        if is_local_host(ollama_host):
            return
        raise SetupFault(
            f"error: --local-only refused OLLAMA_HOST={ollama_host}\n"
            "  That host is not this machine - sending items there IS data leaving.\n"
            "  Point OLLAMA_HOST at localhost (or unset it) to stay inside the fence."
        )
    raise SetupFault(
        f"error: --local-only forbids the cloud {_ROLE_WORDS[role]} wire "
        f"'{ref.provider}/{ref.name}'\n"
        f"  With --local-only, input stays on this machine - "
        f"{ref.provider} is a cloud endpoint.\n"
        f"  {_ROLE_FIXES[role]}"
    )
