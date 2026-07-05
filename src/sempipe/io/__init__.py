"""I/O adapters: stdin/file readers, TTY-adaptive writers, stderr diagnostics.

Boundary rule (plan/architecture.md): only ``writers`` touches stdout; only
``diagnostics`` and ``progress`` touch stderr.
"""

from __future__ import annotations

__all__: list[str] = []
