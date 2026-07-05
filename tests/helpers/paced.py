"""A fake Ollama whose completions the TEST releases — determinism for signal and
streaming tests. Synchronization is by events (arrivals, releases), never sleeps.

Runs threaded inside the test process; the CLI under test is a subprocess pointed at
``server.url`` via ``OLLAMA_HOST``.
"""

from __future__ import annotations

import json
import threading
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from collections.abc import Callable

__all__ = ["PacedOllama"]


class PacedOllama:
    """``/api/tags`` answers instantly; every ``/api/chat`` parks until ``release()``.

    ``reply`` maps the request body (the parsed chat JSON) to the content string of
    the response — key on the *item text inside the prompt*, never on arrival order.
    """

    def __init__(self, reply: Callable[[dict[str, object]], str]) -> None:
        self._reply = reply
        self._gate = threading.Semaphore(0)
        self._arrived = 0
        self._cond = threading.Condition()
        outer = self

        class Handler(BaseHTTPRequestHandler):
            def do_GET(self) -> None:  # /api/tags
                self._respond({"models": [{"name": "qwen3:8b"}]})

            def do_POST(self) -> None:  # /api/chat — park until released
                length = int(self.headers.get("content-length", 0))
                body: dict[str, object] = json.loads(self.rfile.read(length) or b"{}")
                with outer._cond:
                    outer._arrived += 1
                    outer._cond.notify_all()
                outer._gate.acquire()
                self._respond({"message": {"content": outer._reply(body)}})

            def _respond(self, payload: dict[str, object]) -> None:
                data = json.dumps(payload).encode()
                self.send_response(200)
                self.send_header("content-type", "application/json")
                self.send_header("content-length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def log_message(self, format: str, *args: object) -> None:  # noqa: A002 — stdlib signature
                del format, args  # keep test output clean

        self._server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self._server.daemon_threads = True
        self._thread = threading.Thread(target=self._server.serve_forever, daemon=True)

    # -- lifecycle ---------------------------------------------------------------

    def __enter__(self) -> PacedOllama:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._gate.release(64)  # unpark any stragglers so shutdown never hangs
        self._server.shutdown()
        self._server.server_close()

    # -- test API ----------------------------------------------------------------

    @property
    def url(self) -> str:
        host, port = self._server.server_address[:2]
        return f"http://{host}:{port}"

    @property
    def arrived(self) -> int:
        with self._cond:
            return self._arrived

    def wait_for_arrivals(self, n: int, *, timeout: float = 15.0) -> None:
        with self._cond:
            if not self._cond.wait_for(lambda: self._arrived >= n, timeout=timeout):
                raise TimeoutError(f"expected {n} arrivals, saw {self._arrived}")

    def release(self, n: int = 1) -> None:
        self._gate.release(n)
