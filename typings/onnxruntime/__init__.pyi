"""Minimal stubs for the slice of onnxruntime smartpipe touches (models/local_ner)."""

from collections.abc import Mapping

class SessionOptions:
    log_severity_level: int
    def __init__(self) -> None: ...

class InferenceSession:
    def __init__(
        self,
        path_or_bytes: str | bytes,
        sess_options: SessionOptions | None = None,
        providers: list[str] | None = None,
    ) -> None: ...
    def run(self, output_names: object, input_feed: Mapping[str, object]) -> list[object]: ...
