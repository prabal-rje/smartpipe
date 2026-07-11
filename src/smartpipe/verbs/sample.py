"""The ``sample`` verb: seeded representative subsets (D38/08, KQL ``sample``).

Deterministic BY DEFAULT (seed 0): the same input yields the same sample with
no flags, so prompt comparisons compare prompts, and P10's methods section
can cite the sample. Reservoir sampling: one pass, constant memory, free.

``--by FIELD`` (item 65c) stratifies through an owned SQLite spill: counts and
payloads stay on disk, then only the positive-quota reservoirs occupy memory
(at most N rows total). Largest-remainder rounding keeps the result exact.
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


@dataclass(frozen=True, slots=True)
class _StratumKey:
    kind: str
    value: str

    @property
    def token(self) -> str:
        return f"{self.kind}\0{self.value}"


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
    """Disk-backed census, then reservoirs whose combined payload cap is N."""
    assert request.by is not None  # dispatched on the flag
    import sqlite3
    import tempfile

    lacked = 0
    seen = 0
    strata_count = 0
    chosen: list[tuple[int, str]] = []
    with tempfile.TemporaryDirectory(prefix="smartpipe-sample-") as directory:
        connection = sqlite3.connect(f"{directory}/sample.sqlite3")
        try:
            _create_store(connection)
            for line in stdin:
                if not line.strip():
                    continue
                raw = line.removesuffix("\n").removesuffix("\r")
                stratum, missing = _stratum_of(raw, request.by)
                lacked += missing
                _store_row(connection, seen, stratum, raw)
                seen += 1
            connection.commit()
            strata_count = _scalar_int(connection, "SELECT COUNT(*) FROM strata")
            if seen <= request.count:
                chosen = [
                    (_as_int(position), _as_str(payload))
                    for position, payload in connection.execute(
                        "SELECT position, payload FROM rows ORDER BY position"
                    )
                ]
            else:
                _allocate_store(connection, request.count, seen, request.seed)
                chosen = _select_reservoirs(connection, request.count, request.seed)
        finally:
            connection.close()

    for _index, raw in sorted(chosen):  # input order preserved
        stdout.write(raw + "\n")
    if seen <= request.count:
        diagnostics.note(f"sample: input had {seen:,} rows ≤ {request.count:,} — all kept")
        return ExitCode.OK
    assert len(chosen) == request.count  # Hamilton + reservoirs prove exactness
    strata = "stratum" if strata_count == 1 else "strata"
    diagnostics.note(
        f"sample: {len(chosen):,} of {seen:,} "
        f"(seed {request.seed}, {strata_count:,} {strata} by '{request.by}')"
    )
    if lacked:
        diagnostics.note(f"sample: {lacked:,} rows lacked '{request.by}' - grouped as null")
    return ExitCode.OK


def _create_store(connection: object) -> None:
    import sqlite3

    assert isinstance(connection, sqlite3.Connection)
    connection.executescript(
        """
        CREATE TABLE strata (
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            size INTEGER NOT NULL,
            take INTEGER NOT NULL DEFAULT 0,
            PRIMARY KEY (kind, value)
        ) WITHOUT ROWID;
        CREATE TABLE rows (
            position INTEGER PRIMARY KEY,
            kind TEXT NOT NULL,
            value TEXT NOT NULL,
            payload TEXT NOT NULL
        );
        """
    )


def _store_row(connection: object, position: int, key: _StratumKey, payload: str) -> None:
    import sqlite3

    assert isinstance(connection, sqlite3.Connection)
    connection.execute(
        "INSERT INTO rows(position, kind, value, payload) VALUES (?, ?, ?, ?)",
        (position, key.kind, key.value, payload),
    )
    connection.execute(
        """
        INSERT INTO strata(kind, value, size) VALUES (?, ?, 1)
        ON CONFLICT(kind, value) DO UPDATE SET size = size + 1
        """,
        (key.kind, key.value),
    )


def _allocate_store(connection: object, total: int, population: int, seed: int) -> None:
    """Hamilton allocation in SQLite, so a million strata stay off-heap."""
    import sqlite3

    from smartpipe.engine.stratify import tie_digest

    assert isinstance(connection, sqlite3.Connection)

    def rank(kind: str, value: str) -> str:
        return tie_digest(seed, f"{kind}\0{value}")

    connection.create_function("seed_rank", 2, rank, deterministic=True)
    connection.execute(
        "UPDATE strata SET take = CAST((? * size) / ? AS INTEGER)",
        (total, population),
    )
    floors = _scalar_int(connection, "SELECT COALESCE(SUM(take), 0) FROM strata")
    remaining = total - floors
    if remaining:
        winners = [
            (_as_str(kind), _as_str(value))
            for kind, value in connection.execute(
                """
                SELECT kind, value
                FROM strata
                WHERE (? * size) % ? != 0
                ORDER BY ((? * size) % ?) DESC,
                         seed_rank(kind, value) ASC,
                         kind ASC,
                         value ASC
                LIMIT ?
                """,
                (total, population, total, population, remaining),
            )
        ]
        connection.executemany(
            "UPDATE strata SET take = take + 1 WHERE kind = ? AND value = ?",
            winners,
        )
    connection.commit()


def _select_reservoirs(connection: object, total: int, seed: int) -> list[tuple[int, str]]:
    import sqlite3

    assert isinstance(connection, sqlite3.Connection)
    allocations = {
        _StratumKey(_as_str(kind), _as_str(value)): _as_int(take)
        for kind, value, take in connection.execute(
            "SELECT kind, value, take FROM strata WHERE take > 0"
        )
    }
    rng = random.Random(seed)
    sizes: dict[_StratumKey, int] = {}
    reservoirs: dict[_StratumKey, list[tuple[int, str]]] = {key: [] for key in allocations}
    for position, kind, value, payload in connection.execute(
        "SELECT position, kind, value, payload FROM rows ORDER BY position"
    ):
        key = _StratumKey(_as_str(kind), _as_str(value))
        capacity = allocations.get(key)
        if capacity is None:
            continue
        size = sizes.get(key, 0) + 1
        sizes[key] = size
        pool = reservoirs[key]
        pair = (_as_int(position), _as_str(payload))
        if len(pool) < capacity:
            pool.append(pair)
            continue
        slot = rng.randint(0, size - 1)
        if slot < capacity:
            pool[slot] = pair
    chosen = [pair for pool in reservoirs.values() for pair in pool]
    assert len(chosen) == total
    return chosen


def _scalar_int(connection: object, query: str) -> int:
    import sqlite3

    assert isinstance(connection, sqlite3.Connection)
    row = connection.execute(query).fetchone()
    if row is None:
        raise RuntimeError("sample store returned no scalar row")
    return _as_int(row[0])


def _as_int(value: object) -> int:
    if not isinstance(value, int) or isinstance(value, bool):
        raise RuntimeError(f"sample store expected an integer, got {type(value).__name__}")
    return value


def _as_str(value: object) -> str:
    if not isinstance(value, str):
        raise RuntimeError(f"sample store expected text, got {type(value).__name__}")
    return value


def _stratum_of(raw: str, by: str) -> tuple[_StratumKey, int]:
    """The row's stratum key and whether the field was LACKING (vs null).

    A JSON record's field value keys the stratum (non-strings by their JSON
    text); a missing field - and a plain text row, which has no fields - is
    the null stratum, mirroring summarize's null group.
    """
    from smartpipe.engine.fieldpath import MISSING, lookup
    from smartpipe.io.items import item_from_line

    item = item_from_line(raw, 0)
    if item.data is None:
        return _StratumKey("null", ""), 1
    value = lookup(item.data, by)
    if value is MISSING:
        return _StratumKey("null", ""), 1
    if value is None:
        return _StratumKey("null", ""), 0
    if isinstance(value, str):
        return _StratumKey("string", json.dumps(value, ensure_ascii=True)), 0
    if isinstance(value, bool):
        kind = "boolean"
    elif isinstance(value, int):
        kind = "integer"
    elif isinstance(value, float):
        kind = "number"
    elif isinstance(value, list):
        kind = "array"
    else:
        kind = "object"
    return _StratumKey(
        kind,
        json.dumps(value, sort_keys=True, ensure_ascii=True, separators=(",", ":")),
    ), 0
