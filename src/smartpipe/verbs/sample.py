"""The ``sample`` verb: seeded representative subsets (D38/08, KQL ``sample``).

Deterministic BY DEFAULT (seed 0): the same input yields the same sample with
no flags, so prompt comparisons compare prompts, and P10's methods section
can cite the sample. Reservoir sampling: one pass, constant memory, free.
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ExitCode, UsageFault
from smartpipe.io import diagnostics

if TYPE_CHECKING:
    from typing import TextIO

__all__ = ["SampleRequest", "run_sample"]


@dataclass(frozen=True, slots=True)
class SampleRequest:
    count: int
    seed: int = 0


def run_sample(request: SampleRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    if request.count < 1:
        raise UsageFault("sample needs a positive count")
    rng = random.Random(request.seed)  # local instance — never the global RNG
    reservoir: list[tuple[int, str]] = []
    seen = 0
    for line in stdin:
        if not line.strip():
            continue
        raw = line.removesuffix("\n").removesuffix("\r")
        if seen < request.count:
            reservoir.append((seen, raw))
        else:
            slot = rng.randint(0, seen)
            if slot < request.count:
                reservoir[slot] = (seen, raw)
        seen += 1
    for _index, raw in sorted(reservoir):  # input order preserved
        stdout.write(raw + "\n")
    if seen <= request.count:
        diagnostics.note(f"sample: input had {seen:,} rows ≤ {request.count:,} — all kept")
    else:
        diagnostics.note(f"sample: {len(reservoir):,} of {seen:,} (seed {request.seed})")
    return ExitCode.OK
