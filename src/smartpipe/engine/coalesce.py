"""Request-coalescing math (item 62) — pure.

N small items normally cost N model calls. The coalescer groups eligible
submissions by (model, return shape) and flies each group as ONE request:
K labeled ``<input id="rN">`` blocks (item 57's ``render_input`` framing,
numbered) under a shared preamble, answered by ONE object keyed ``r1..rK``.
An object — never an array — because a missing key names exactly which item
to retry; array positions lose that alignment.

This module owns everything decidable without I/O: eligibility, the coalesce
key, group caps, label assignment, packed-prompt composition, batch-schema
composition, and the salvage split of a packed reply. The async plumbing that
owns time and the wire lives in ``models/coalesce``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from typing import TYPE_CHECKING

from smartpipe.core.errors import ItemError
from smartpipe.core.jsontools import as_record
from smartpipe.engine.chunking import budget_for, estimate_tokens
from smartpipe.engine.schema import validate_and_coerce
from smartpipe.models.base import BatchHint, CompletionRequest

if TYPE_CHECKING:
    from collections.abc import Mapping, Sequence

__all__ = [
    "GROUP_CEILING",
    "PROPERTY_CEILING",
    "WINDOW_SECONDS",
    "BatchSettings",
    "Resend",
    "Salvaged",
    "SplitOutcome",
    "coalesce_key",
    "eligible",
    "labels",
    "max_group",
    "pack",
    "pack_budget",
    "split_reply",
    "submission_tokens",
]

GROUP_CEILING = 12  # K: items per packed call (SMARTPIPE_BATCH_SIZE overrides)
WINDOW_SECONDS = 0.075  # the coalesce window — streams stay live (SMARTPIPE_BATCH_WINDOW_MS)
PROPERTY_CEILING = 40  # fields_per_item x K stays under strict wires' property explosion point
_PACK_OVERHEAD_TOKENS = 800  # preamble + fence labels + batch-schema growth headroom
# Output ceiling for a packed call: 8192 is what the solo structured path already
# sends on every wire today, so it is known-safe everywhere. A truncated packed
# reply is not fatal — the salvage split re-runs whatever keys it cut off.
_PACKED_MAX_TOKENS = 8192

_PLAIN_ITEM_SCHEMA: dict[str, object] = {"type": "string"}
_ANY_OBJECT: dict[str, object] = {"type": "object"}

_FENCE_OPEN = "<input>\n"
_FENCE_CLOSE = "\n</input>"

_PREAMBLE = (
    "This request packs {count} independent inputs, each in its own "
    '<input id="..."> block. Handle every input completely separately - '
    "never let one input influence another. Reply with ONLY one JSON object "
    "keyed by input id ({first} through {last}), where each value is that "
    "input's full answer. Answer for EVERY id."
)
_PER_ITEM_NOTE = (
    "Each input block starts with its own 'instruction:' line - apply that "
    "instruction to that input only."
)


@dataclass(frozen=True, slots=True)
class BatchSettings:
    """The run's coalescing posture, resolved once at the composition root."""

    size: int = GROUP_CEILING
    window_seconds: float = WINDOW_SECONDS


@dataclass(frozen=True, slots=True)
class Salvaged:
    """One labeled key answered validly — its reply rides to the waiter as if
    the item had been asked solo."""

    reply: str


@dataclass(frozen=True, slots=True)
class Resend:
    """One labeled key missing or invalid — the item re-runs SOLO through the
    existing single-item path (which owns the repair ladder). Never re-batched."""

    reason: str


SplitOutcome = Salvaged | Resend


def max_group(per_item_schema: Mapping[str, object] | None, ceiling: int = GROUP_CEILING) -> int:
    """The largest K this return shape admits: ``fields_per_item x K`` must stay
    under ``PROPERTY_CEILING`` (strict structured-output wires reject property
    explosions); plain replies count as one field."""
    fields = 1
    if per_item_schema is not None:
        properties = as_record(per_item_schema.get("properties"))
        if properties is not None:
            fields = max(1, len(properties))
    return min(ceiling, PROPERTY_CEILING // fields)


def eligible(request: CompletionRequest, ceiling: int = GROUP_CEILING) -> bool:
    """Whether a request may coalesce: it opted in (the verb attached a
    ``BatchHint``), carries no media, and its return shape admits a group of
    at least two — a group of one is just a slower solo call."""
    if request.batch is None or request.media:
        return False
    return max_group(request.json_schema, ceiling) >= 2


def coalesce_key(request: CompletionRequest) -> str:
    """Requests group only when everything but the item itself matches —
    the (model, return type/schema) rule; the model is fixed per coalescer,
    so the key covers the return shape and the sampling knobs."""
    payload: dict[str, object] = {
        "system": request.system,
        "schema": None if request.json_schema is None else dict(request.json_schema),
        "max_tokens": request.max_tokens,
        "temperature": request.temperature,
        "presence": request.presence_penalty,
        "frequency": request.frequency_penalty,
    }
    return json.dumps(payload, sort_keys=True, separators=(",", ":"))


def labels(count: int) -> tuple[str, ...]:
    return tuple(f"r{position}" for position in range(1, count + 1))


def submission_tokens(request: CompletionRequest) -> int:
    """One submission's share of the packed request, for the token-budget cap."""
    hint = request.batch
    assert hint is not None, "submission_tokens needs an eligible request"
    return estimate_tokens(hint.instruction) + estimate_tokens(hint.payload)


def pack_budget(provider: str) -> int:
    """Token budget for one packed request on this provider's wire."""
    return budget_for(provider, prompt_overhead=_PACK_OVERHEAD_TOKENS)


def pack(requests: Sequence[CompletionRequest]) -> CompletionRequest:
    """Compose K eligible requests into ONE. A constant instruction is lifted
    into the shared prompt once (prompt-prefix caching wins); varying
    instructions ride inside their own labeled input blocks."""
    assert len(requests) >= 2, "a pack of one is a solo call"
    hints = [_hint_of(request) for request in requests]
    base = requests[0]
    names = labels(len(requests))
    lifted = len({hint.instruction for hint in hints}) == 1
    blocks = [
        _labeled_block(hint, name, lift=lifted) for hint, name in zip(hints, names, strict=True)
    ]
    preamble = _PREAMBLE.format(count=len(requests), first=names[0], last=names[-1])
    if not lifted:
        preamble = f"{preamble} {_PER_ITEM_NOTE}"
    system = f"{base.system}\n\n{preamble}" if base.system else preamble
    joined = "\n\n".join(blocks)
    user = f"{hints[0].instruction}\n\n{joined}" if lifted else joined
    return CompletionRequest(
        system=system,
        user=user,
        json_schema=batch_schema(base.json_schema, names),
        max_tokens=min(base.max_tokens * len(requests), _PACKED_MAX_TOKENS),
        temperature=base.temperature,
        presence_penalty=base.presence_penalty,
        frequency_penalty=base.frequency_penalty,
    )


def batch_schema(
    per_item_schema: Mapping[str, object] | None, names: Sequence[str]
) -> dict[str, object]:
    """The packed reply's shape: an OBJECT keyed by input id, every id required,
    nothing extra — hand-rolled like every schema in this codebase. Plain-mode
    items answer as strings."""
    per_item: dict[str, object] = (
        dict(per_item_schema) if per_item_schema is not None else dict(_PLAIN_ITEM_SCHEMA)
    )
    return {
        "type": "object",
        "properties": dict.fromkeys(names, per_item),
        "required": list(names),
        "additionalProperties": False,
    }


def split_reply(
    reply: str,
    names: Sequence[str],
    per_item_schema: Mapping[str, object] | None,
) -> tuple[SplitOutcome, ...]:
    """The salvage split: every present key validates INDIVIDUALLY against the
    per-item schema; valid keys become that item's reply, everything else is a
    named ``Resend``. An unreadable reply (not JSON, not an object) resends
    every member — nothing is guessed from alignment."""
    try:
        record = validate_and_coerce(reply, _ANY_OBJECT)
    except ItemError as fault:
        unreadable = Resend(f"batched reply unreadable: {fault}")
        return tuple(unreadable for _name in names)
    return tuple(_key_outcome(record, name, per_item_schema) for name in names)


def _key_outcome(
    record: Mapping[str, object],
    name: str,
    per_item_schema: Mapping[str, object] | None,
) -> SplitOutcome:
    if name not in record:
        return Resend(f"key {name!r} missing from the batched reply")
    value = record[name]
    if per_item_schema is None:  # plain mode: the answer is the transformed text
        if isinstance(value, str):
            return Salvaged(value)
        return Resend(f"key {name!r} is not text")
    sub = as_record(value)
    if sub is None:
        return Resend(f"key {name!r} is not an object")
    dumped = json.dumps(sub, ensure_ascii=False)
    try:
        # validity check only — the waiter re-validates (and re-coerces, with
        # the ambiguous-date disclosure) exactly as the solo path would
        validate_and_coerce(dumped, per_item_schema)
    except ItemError as fault:
        return Resend(f"key {name!r} invalid: {fault}")
    return Salvaged(dumped)


def _hint_of(request: CompletionRequest) -> BatchHint:
    hint = request.batch
    assert hint is not None, "pack() only receives eligible requests"
    return hint


def _labeled_block(hint: BatchHint, name: str, *, lift: bool) -> str:
    """One item's labeled block: the ``render_input`` fence (item 57), numbered.
    When instructions vary per item, the item's own instruction rides inside
    its block; a lifted (constant) instruction appears once, outside."""
    body = hint.payload
    if body:
        assert body.startswith(_FENCE_OPEN) and body.endswith(_FENCE_CLOSE), body
        body = body[len(_FENCE_OPEN) : -len(_FENCE_CLOSE)]
    lines = [f'<input id="{name}">']
    if not lift:
        lines.append(f"instruction: {hint.instruction}")
        if body:
            lines.append("")
    if body:
        lines.append(body)
    lines.append("</input>")
    return "\n".join(lines)
