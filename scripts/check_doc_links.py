"""Internal doc-link checker (release matrix row 4): every relative markdown link
in docs/, README, CONTRIBUTING, RELEASING, MAINTENANCE must resolve. External
URLs are out of scope (no network in gates)."""

from __future__ import annotations

import re
import sys
from pathlib import Path

_LINK = re.compile(r"\[[^\]]*\]\(([^)#\s]+)(#[^)\s]*)?\)")

ROOTS = [
    "README.md",
    "CONTRIBUTING.md",
    "RELEASING.md",
    "MAINTENANCE.md",
    *Path("docs").rglob("*.md"),
]


def main() -> int:
    broken: list[str] = []
    for source in ROOTS:
        path = Path(source)
        for match in _LINK.finditer(path.read_text(encoding="utf-8")):
            target = match.group(1)
            if target.startswith(("http://", "https://", "mailto:")):
                continue
            resolved = (path.parent / target).resolve()
            if not resolved.exists():
                broken.append(f"{path}: {target}")
    if broken:
        print("broken internal links:")
        for line in broken:
            print(f"  {line}")
        return 1
    print(f"all internal doc links resolve OK ({len(ROOTS)} files scanned)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
