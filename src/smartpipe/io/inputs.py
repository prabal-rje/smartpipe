"""Where items come from (spec §8): stdin lines, a glob of files, or a list of
filenames on stdin. ``InputSpec`` captures the flags; ``expand_globs`` resolves
``--in`` patterns to a sorted, de-duplicated file list.
"""

from __future__ import annotations

import glob
import os
from dataclasses import dataclass
from pathlib import Path

from smartpipe.core.errors import UsageFault

__all__ = ["STDIN", "InputSpec", "expand_globs"]


@dataclass(frozen=True, slots=True)
class InputSpec:
    patterns: tuple[str, ...]  # --in globs (may be empty)
    from_files: bool  # --from-files: each stdin line names a file
    as_mode: str | None = None  # --as file|lines|jsonl; None = auto (item 15)
    strict_rows: bool = False  # --strict-rows: a mixed record/text stream is an error (item 20)


# The default: no file flags → read stdin lines. Shared because it's immutable.
STDIN = InputSpec(patterns=(), from_files=False)


def expand_globs(patterns: tuple[str, ...]) -> list[Path]:
    """Resolve ``--in`` patterns to existing files, sorted, first-seen deduped.
    An all-empty match is a usage error (exit 64) — silence would look like success."""
    seen: dict[str, Path] = {}
    for pattern in patterns:
        expanded = os.path.expanduser(pattern)
        for match in sorted(glob.glob(expanded, recursive=True)):
            path = Path(match)
            if path.is_file():
                seen.setdefault(str(path), path)
    if not seen:
        joined = " ".join(patterns)
        raise UsageFault(
            f"no files matched: {joined}\n"
            "  check the pattern, and quote it so the shell doesn't expand it first: --in '*.pdf'"
        )
    return list(seen.values())
