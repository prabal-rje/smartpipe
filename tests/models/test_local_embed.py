"""The local embedding wire (D44): one engine, typed boundary, honest failures."""

from __future__ import annotations

import pytest

from smartpipe.core.errors import ItemError
from smartpipe.models.base import ModelRef, parse_model_ref
from smartpipe.models.local_embed import LocalEmbeddingModel


class FakeArray:
    """numpy-shaped: exposes tolist(), like fastembed's output rows."""

    def __init__(self, values: list[float]) -> None:
        self.values = values

    def tolist(self) -> list[float]:
        return self.values


class FakeEngine:
    def __init__(self, rows: list[object]) -> None:
        self.rows = rows
        self.calls = 0

    def embed(self, texts: list[str]) -> list[object]:
        self.calls += 1
        return self.rows[: len(texts)]


def _model(engine: object) -> LocalEmbeddingModel:
    model = LocalEmbeddingModel(ref=ModelRef("local", "nomic-embed-text-v1.5"))
    model.engine = engine  # inject — tests never download onnx weights
    return model


async def test_numpy_rows_become_plain_float_tuples() -> None:
    model = _model(FakeEngine([FakeArray([0.1, 0.2]), FakeArray([0.3, 0.4])]))
    vectors = await model.embed(["a", "b"])
    assert vectors == ((0.1, 0.2), (0.3, 0.4))


async def test_engine_loads_once_and_is_reused() -> None:
    engine = FakeEngine([FakeArray([1.0])])
    model = _model(engine)
    await model.embed(["a"])
    await model.embed(["b"])
    assert engine.calls == 2  # same engine served both batches
    assert model.engine is engine  # never reloaded


async def test_engine_failure_is_a_per_item_error() -> None:
    class Explodes:
        def embed(self, texts: list[str]) -> list[object]:
            raise RuntimeError("onnx says no")

    with pytest.raises(ItemError, match="local embedding failed"):
        await _model(Explodes()).embed(["a"])


async def test_unexpected_shape_is_loud() -> None:
    model = _model(FakeEngine([FakeArray(["not", "floats"])]))  # type: ignore[list-item]
    with pytest.raises(ItemError, match="unexpected shape"):
        await model.embed(["a"])


def test_default_ref_parses_to_the_local_provider() -> None:
    ref = parse_model_ref("local/nomic-embed-text-v1.5")
    assert ref.provider == "local"
    assert ref.name == "nomic-embed-text-v1.5"
