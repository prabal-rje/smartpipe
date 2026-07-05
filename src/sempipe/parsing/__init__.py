"""File parsing: detect a file's kind, extract its text (spec §3.1, D08).

The user never names a parser. ``detect`` sniffs the kind (pure, never raises);
``extract`` turns it into text, lazy-importing the optional markitdown bridge only
when a document actually needs it.
"""

from __future__ import annotations

__all__: list[str] = []
