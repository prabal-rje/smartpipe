"""Read and write ``config.toml``.

Rules (plan/decisions.md D09): the file is optional; unknown keys are ignored
(forward compatibility); wrong-typed values fail loudly with the key named;
API keys are never stored here.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass
from typing import TYPE_CHECKING

import tomli_w

from sempipe.config.paths import human_path
from sempipe.core.errors import SetupFault

if TYPE_CHECKING:
    from collections.abc import Mapping
    from pathlib import Path

__all__ = ["Config", "load_config", "save_config"]


@dataclass(frozen=True, slots=True)
class Config:
    model: str | None = None
    embed_model: str | None = None
    concurrency: int | None = None
    output: str | None = None


def load_config(path: Path) -> Config:
    if not path.exists():
        return Config()
    try:
        data = tomllib.loads(path.read_text(encoding="utf-8"))
    except tomllib.TOMLDecodeError as exc:
        raise SetupFault(_broken_screen(path, exc)) from exc
    return Config(
        model=_string(data, "model", path),
        embed_model=_string(data, "embed-model", path),
        concurrency=_positive_int(data, "concurrency", path),
        output=_string(data, "output", path),
    )


def save_config(path: Path, config: Config) -> None:
    document: dict[str, str | int] = {}
    if config.model is not None:
        document["model"] = config.model
    if config.embed_model is not None:
        document["embed-model"] = config.embed_model
    if config.concurrency is not None:
        document["concurrency"] = config.concurrency
    if config.output is not None:
        document["output"] = config.output
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(tomli_w.dumps(document), encoding="utf-8")


def _string(data: Mapping[str, object], key: str, path: Path) -> str | None:
    value = data.get(key)
    if value is None:
        return None
    if not isinstance(value, str):
        raise SetupFault(_wrong_type_screen(path, key, "a string", value))
    return value


def _positive_int(data: Mapping[str, object], key: str, path: Path) -> int | None:
    value = data.get(key)
    if value is None:
        return None
    if isinstance(value, bool) or not isinstance(value, int) or value < 1:
        raise SetupFault(_wrong_type_screen(path, key, "a whole number ≥ 1", value))
    return value


def _broken_screen(path: Path, exc: tomllib.TOMLDecodeError) -> str:
    detail = str(exc)
    located = re.search(r"at line (\d+)", detail)
    location = f", line {located.group(1)}" if located else ""
    detail = re.sub(r"\s*\(at line [^)]*\)$", "", detail)
    return (
        "error: config file has a syntax error\n"
        f"  {human_path(path)}{location}: {detail}\n"
        "  Fix the line, or start fresh: sempipe config"
    )


def _wrong_type_screen(path: Path, key: str, expected: str, value: object) -> str:
    return (
        f"error: config value '{key}' should be {expected}\n"
        f"  {human_path(path)} has: {key} = {value!r}\n"
        f"  Fix the line, or reset it: sempipe config"
    )
