"""An environment fence for imports with env side effects.

markitdown's import chain pulls in magika, whose ``__init__`` calls
``dotenv.load_dotenv(dotenv.find_dotenv())`` — silently reading a ``.env``
found in (or above) the working directory into ``os.environ``. smartpipe's
precedence contract says the environment always wins BECAUSE exporting a
variable is a consented act; a file sitting on disk is not consent. Anything
an import adds or changes inside the fence is reverted on exit.

(Root cause of the auth-list test flake, plan ledger item 78.)
"""

from __future__ import annotations

import os
from contextlib import contextmanager
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Generator

__all__ = ["environ_fence"]


@contextmanager
def environ_fence() -> Generator[None]:
    """Snapshot ``os.environ``; on exit, delete additions and restore changes."""
    snapshot = dict(os.environ)
    try:
        yield
    finally:
        for key in set(os.environ) - snapshot.keys():
            del os.environ[key]
        for key, value in snapshot.items():
            if os.environ.get(key) != value:
                os.environ[key] = value
