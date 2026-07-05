"""One place where respx's untyped call log crosses into typed test code."""

from __future__ import annotations

import json
from typing import TYPE_CHECKING, Any, cast

if TYPE_CHECKING:
    import respx

__all__ = ["sent_form", "sent_header", "sent_json"]


def _request(route: respx.Route, index: int) -> Any:  # respx's Call.request is untyped
    return cast("Any", route).calls[index].request


def sent_json(route: respx.Route, index: int = 0) -> object:
    """The JSON body of the index-th request this route captured."""
    return json.loads(_request(route, index).content)


def sent_header(route: respx.Route, name: str, index: int = 0) -> str:
    """A request header value from the index-th captured call, typed as str."""
    return str(_request(route, index).headers[name])


def sent_form(route: respx.Route, index: int = 0) -> dict[str, str]:
    """A urlencoded request body from the index-th call, decoded to a flat dict."""
    from urllib.parse import parse_qs

    content = str(_request(route, index).content.decode())
    return {key: values[0] for key, values in parse_qs(content).items()}
