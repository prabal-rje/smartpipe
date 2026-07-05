"""One place where respx's untyped call log crosses into typed test code."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import respx

__all__ = ["sent_json"]


def sent_json(route: respx.Route, index: int = 0) -> object:
    """The JSON body of the index-th request this route captured."""
    call = route.calls[index]  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    content = call.request.content  # pyright: ignore[reportUnknownMemberType, reportUnknownVariableType]
    return json.loads(content)  # pyright: ignore[reportUnknownArgumentType]
