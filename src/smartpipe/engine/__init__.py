"""The pure functional core: prompts, schema, chunking, ranking, ordering.

Nothing in this package does I/O, reads the clock, or touches the environment —
every input is a parameter. That is what makes it exhaustively testable.
"""

from __future__ import annotations

__all__: list[str] = []
