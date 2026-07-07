"""``sempipe usage`` — the ledger of what the meter observed (D41)."""

from __future__ import annotations

import os

import click

from sempipe.io.metering import count as count_fmt
from sempipe.io.metering import duration as duration_fmt
from sempipe.io.metering import megabytes as megabytes_fmt
from sempipe.io.usage import Totals, read_ledger, reset_ledger, stamp

__all__ = ["usage_command"]


@click.group(name="usage", invoke_without_command=True)
@click.pass_context
def usage_command(ctx: click.Context) -> None:
    """Model usage over time: hour, day, week, month, lifetime. Resettable.

    Counts model-touching runs only (free verbs never call a model). The
    numbers are the meter's observed units — tokens, media, conversions.
    """
    if ctx.invoked_subcommand is not None:
        return
    windows, first_seen, last_reset = read_ledger(os.environ)
    if windows["lifetime"].runs == 0:
        click.echo("no model usage recorded yet")
        return
    header = (
        f"{'':<12}{'runs':>6}{'tokens in':>12}{'tokens out':>12}"
        f"{'media':>10}{'audio':>9}{'conv':>6}"
    )
    click.echo(header)
    for name in ("past hour", "past day", "past week", "past month", "lifetime"):
        click.echo(_row(name, windows[name]))
    notes: list[str] = []
    if last_reset is not None:
        notes.append(f"last reset {stamp(last_reset)}")
    if first_seen is not None:
        notes.append(f"first use {stamp(first_seen)}")
    if notes:
        click.echo("since: " + " · ".join(notes))


def _row(name: str, totals: Totals) -> str:
    audio = duration_fmt(totals.audio_seconds) if totals.audio_seconds else "-"
    media = megabytes_fmt(totals.media_bytes) if totals.media_bytes else "-"
    return (
        f"{name:<12}{totals.runs:>6}{count_fmt(totals.tokens_in):>12}"
        f"{count_fmt(totals.tokens_out):>12}{media:>10}{audio:>9}{totals.conversions:>6}"
    )


@usage_command.command(name="reset")
def usage_reset() -> None:
    """Zero the ledger; the reset time is remembered and shown."""
    previous = reset_ledger(os.environ)
    click.echo(
        f"usage reset (previous lifetime: {count_fmt(previous.tokens_in)} in · "
        f"{count_fmt(previous.tokens_out)} out tokens across {previous.runs} runs)"
    )
