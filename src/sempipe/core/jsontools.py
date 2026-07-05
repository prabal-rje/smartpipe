"""Typed access into untrusted JSON (provider replies), without ``cast``.

Each helper narrows ``object`` to a concrete shape or returns ``None`` — the
caller decides whether a wrong shape is an ``ItemError`` or a bug.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, TypeGuard

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = ["as_float_vector", "as_items", "as_record", "as_str", "record_at"]


def _is_record(value: object) -> TypeGuard[Mapping[str, object]]:
    return isinstance(value, dict)  # json.loads keys are str by contract


def _is_items(value: object) -> TypeGuard[Sequence[object]]:
    return isinstance(value, list)


def as_record(value: object) -> Mapping[str, object] | None:
    return value if _is_record(value) else None


def as_items(value: object) -> Sequence[object] | None:
    return value if _is_items(value) else None


def as_str(value: object) -> str | None:
    return value if isinstance(value, str) else None


def as_float_vector(value: object) -> tuple[float, ...] | None:
    items = as_items(value)
    if items is None:
        return None
    vector: list[float] = []
    for element in items:
        if isinstance(element, bool) or not isinstance(element, int | float):
            return None
        vector.append(float(element))
    return tuple(vector)


def record_at(value: object, *keys: str) -> Mapping[str, object] | None:
    """Walk nested objects: ``record_at(data, "message")`` → the message record."""
    current = as_record(value)
    for key in keys:
        if current is None:
            return None
        current = as_record(current.get(key))
    return current
