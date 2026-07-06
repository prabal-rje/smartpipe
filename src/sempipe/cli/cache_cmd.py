"""``sempipe cache`` — maintenance for the result cache (D38/15)."""

from __future__ import annotations

import os
import shutil
from pathlib import Path

import click

__all__ = ["cache_command"]


def _cache_dir() -> Path:
    base = os.environ.get("XDG_CACHE_HOME", "").strip()
    root = Path(base) if base else Path.home() / ".cache"
    return root / "sempipe" / "results"


@click.group(name="cache")
def cache_command() -> None:
    """Inspect or clear the result cache (enable with: sempipe config cache on)."""


@cache_command.command(name="clear")
def cache_clear() -> None:
    """Delete every cached reply and report the space freed."""
    directory = _cache_dir()
    if not directory.exists():
        click.echo("cache is empty")
        return
    size = sum(entry.stat().st_size for entry in directory.rglob("*") if entry.is_file())
    shutil.rmtree(directory)
    click.echo(f"cache cleared: {size / 1_048_576:.1f} MB")
