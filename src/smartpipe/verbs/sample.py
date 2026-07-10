"""The ``sample`` verb: seeded representative subsets (D38/08, KQL ``sample``).

Deterministic BY DEFAULT (seed 0): the same input yields the same sample with
no flags, so prompt comparisons compare prompts, and P10's methods section
can cite the sample. Reservoir sampling: one pass, constant memory, free.

``--by FIELD`` (item 65c) stratifies: one reservoir per field value, then
proportional allocation with largest-remainder rounding so the total is
exactly N. Rows lacking the field form their own null stratum (summarize's
null-group convention); allocation can never exceed a stratum's size (see
``engine/stratify``), so nothing silently goes missing.
"""

from __future__ import annotations

import json
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
    by: str | None = None  # --by FIELD: stratified, proportional per value


def run_sample(request: SampleRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    if request.count < 1:
        raise UsageFault("sample needs a positive count")
    if request.by is not None:
        return _run_stratified(request, stdin=stdin, stdout=stdout)
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


def _run_stratified(request: SampleRequest, *, stdin: TextIO, stdout: TextIO) -> ExitCode:
    """One capped reservoir per stratum, then largest-remainder allocation."""
    assert request.by is not None  # dispatched on the flag
    rng = random.Random(request.seed)  # ONE stream, shared across strata: same
    # input + same seed = same sample, exactly the no-flag contract
    reservoirs: dict[str | None, list[tuple[int, str]]] = {}  # first-seen order
    sizes: dict[str | None, int] = {}
    lacked = 0
    seen = 0
    for line in stdin:
        if not line.strip():
            continue
        raw = line.removesuffix("\n").removesuffix("\r")
        stratum, missing = _stratum_of(raw, request.by)
        lacked += missing
        size = sizes[stratum] = sizes.get(stratum, 0) + 1
        pool = reservoirs.setdefault(stratum, [])
        if len(pool) < request.count:
            pool.append((seen, raw))
        else:
            slot = rng.randint(0, size - 1)
            if slot < request.count:
                pool[slot] = (seen, raw)
        seen += 1
    if seen <= request.count:
        for _index, raw in sorted(pair for pool in reservoirs.values() for pair in pool):
            stdout.write(raw + "\n")
        diagnostics.note(f"sample: input had {seen:,} rows ≤ {request.count:,} — all kept")
        return ExitCode.OK
    from smartpipe.engine.stratify import allocate

    taken = allocate(request.count, sizes)
    chosen: list[tuple[int, str]] = []
    for stratum, pool in reservoirs.items():
        take = taken[stratum]
        chosen.extend(pool if take >= len(pool) else rng.sample(pool, take))
    for _index, raw in sorted(chosen):  # input order preserved
        stdout.write(raw + "\n")
    strata = "stratum" if len(sizes) == 1 else "strata"
    diagnostics.note(
        f"sample: {len(chosen):,} of {seen:,} "
        f"(seed {request.seed}, {len(sizes):,} {strata} by '{request.by}')"
    )
    if lacked:
        diagnostics.note(f"sample: {lacked:,} rows lacked '{request.by}' - grouped as null")
    return ExitCode.OK


def _stratum_of(raw: str, by: str) -> tuple[str | None, int]:
    """The row's stratum key and whether the field was LACKING (vs null).

    A JSON record's field value keys the stratum (non-strings by their JSON
    text); a missing field - and a plain text row, which has no fields - is
    the null stratum, mirroring summarize's null group.
    """
    from smartpipe.engine.fieldpath import MISSING, lookup
    from smartpipe.io.items import item_from_line

    item = item_from_line(raw, 0)
    if item.data is None:
        return None, 1
    value = lookup(item.data, by)
    if value is MISSING:
        return None, 1
    if value is None:
        return None, 0
    if isinstance(value, str):
        return value, 0
    return json.dumps(value, sort_keys=True, ensure_ascii=False), 0
