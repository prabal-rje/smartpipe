"""Multi-line UX screens, verbatim from plan/ux.md (golden-pinned in tests)."""

from __future__ import annotations

__all__ = ["WELCOME"]

WELCOME = """\
sempipe — semantic pipes for your terminal

  map      Transform each item with a prompt
  filter   Keep items matching a semantic condition
  embed    Convert items to vector embeddings
  top_k    Rank items by similarity to a query
  reduce   Synthesize many items into one
  config   Configure models and settings

Get started:
  sempipe config                                     Interactive setup
  echo "hello" | sempipe map "translate to Spanish"

'sempipe <command> --help' shows examples for each command.
"""
