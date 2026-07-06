"""``sempipe doctor`` — every no-cost setup check, one screen, exit 0/1 (D18).

The report is the result, so it goes to stdout. Never a paid model call: the only
network touch is the existing free 2 s Ollama probe. Key lines report presence,
never values (validating a key costs a call — that's the live runbook's job).
"""

from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

import click

from sempipe.config.doctor import CheckResult, doctor_exit_code, render_report
from sempipe.config.paths import config_path, human_path
from sempipe.core.errors import ExitCode, SempipeError

if TYPE_CHECKING:
    from collections.abc import Mapping

    from sempipe.config.store import Config

__all__ = ["doctor_command"]

_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY")
_EXTRAS = (("files", "markitdown"), ("audio", "speech_recognition"), ("anthropic", "anthropic"))
_RC_FILES = {"zsh": "~/.zshrc", "bash": "~/.bashrc"}


@click.command(name="doctor")
@click.option(
    "--probe",
    is_flag=True,
    help="Also send 4 tiny PAID calls to chart which modalities really work.",
)
def doctor_command(probe: bool) -> None:
    """Check that sempipe is set up and ready — without spending a model call.

    \b
    Verifies: config parses · Ollama reachable · configured models installed ·
    API keys present (never printed) · ChatGPT login · optional extras ·
    shell completions. Exit 0 when everything is green.
    --probe adds the modality matrix: real (tiny) calls, announced first.
    """
    results = asyncio.run(_gather(os.environ))
    click.echo(render_report(results))
    if probe:
        from sempipe.cli.probe_cmd import run_probe

        click.echo("")
        click.echo(asyncio.run(run_probe(os.environ)))
    code = doctor_exit_code(results)
    if code is not ExitCode.OK:
        raise SystemExit(int(code))


async def _gather(env: Mapping[str, str]) -> list[CheckResult]:
    config, config_result = _check_config(env)
    results = [config_result]
    names = await _probe(env)
    results.append(_check_ollama(env, names))
    results.append(_check_model("chat", _effective_chat(env, config), names))
    results.append(_check_model("embed", _effective_embed(env, config), names))
    results.append(_check_keys(env))
    results.append(_check_login(env))
    results.append(_check_extras())
    results.append(_check_completions(env))
    return results


def _check_config(env: Mapping[str, str]) -> tuple[Config | None, CheckResult]:
    from sempipe.config.store import load_config

    path = config_path(env)
    try:
        config = load_config(path)
    except SempipeError as exc:  # doctor reports sickness, it doesn't die of it
        summary = str(exc).splitlines()[0].removeprefix("error: ")
        return None, CheckResult("config", "fail", f"{human_path(path)}: {summary}")
    if not path.exists():
        return config, CheckResult("config", "skip", "no config file (defaults apply)")
    described = config.model or "no default model"
    return config, CheckResult("config", "ok", f"{human_path(path)} parses (model: {described})")


async def _probe(env: Mapping[str, str]) -> tuple[str, ...] | None:
    from sempipe.models.http_support import make_client
    from sempipe.models.ollama import ollama_model_names, resolve_host

    async with make_client() as client:
        return await ollama_model_names(client, resolve_host(env))


def _check_ollama(env: Mapping[str, str], names: tuple[str, ...] | None) -> CheckResult:
    from sempipe.models.ollama import resolve_host

    host = resolve_host(env)
    if names is None:
        return CheckResult(
            "ollama", "fail", f"not reachable at {host} — fix: ollama serve (or set OLLAMA_HOST)"
        )
    return CheckResult("ollama", "ok", f"reachable at {host} ({len(names)} models)")


def _effective_chat(env: Mapping[str, str], config: Config | None) -> str | None:
    return env.get("SEMPIPE_MODEL", "").strip() or (config.model if config else None)


def _effective_embed(env: Mapping[str, str], config: Config | None) -> str | None:
    configured = env.get("SEMPIPE_EMBED_MODEL", "").strip() or (
        config.embed_model if config else None
    )
    return configured or "nomic-embed-text"  # the documented default


def _check_model(
    section: str, configured: str | None, names: tuple[str, ...] | None
) -> CheckResult:
    from sempipe.core.errors import UsageFault
    from sempipe.models.base import parse_model_ref

    if configured is None:
        return CheckResult(
            section, "fail", "no model configured — fix: sempipe config model ollama/qwen3:8b"
        )
    try:
        ref = parse_model_ref(configured)
    except UsageFault as exc:
        return CheckResult(section, "fail", str(exc).splitlines()[0])
    if ref.provider != "ollama":
        return CheckResult(section, "skip", f"{ref} is a cloud model (key presence below)")
    if names is None:
        return CheckResult(section, "skip", f"{ref.name} — can't verify, Ollama unreachable")
    if ref.name in names:
        return CheckResult(section, "ok", f"{configured} is installed")
    return CheckResult(
        section, "fail", f"{ref.name} not in ollama list — fix: ollama pull {ref.name}"
    )


def _check_keys(env: Mapping[str, str]) -> CheckResult:
    parts = (f"{var} {'set' if env.get(var, '').strip() else 'not set'}" for var in _KEY_VARS)
    return CheckResult("keys", "skip", " · ".join(parts))


def _check_login(env: Mapping[str, str]) -> CheckResult:
    from sempipe.config.credentials import credentials_path, load_oauth

    if load_oauth(credentials_path(env), "openai") is not None:
        return CheckResult("login", "ok", "ChatGPT login present (refreshes automatically)")
    return CheckResult("login", "skip", "no ChatGPT login — optional: sempipe auth login")


def _check_extras() -> CheckResult:
    from importlib.util import find_spec

    installed = {extra: find_spec(module) is not None for extra, module in _EXTRAS}
    if all(installed.values()):
        return CheckResult("extras", "ok", " · ".join(installed))
    marks = " · ".join(f"{extra} {'✓' if present else '✗'}" for extra, present in installed.items())
    first_missing = next(extra for extra, present in installed.items() if not present)
    return CheckResult(
        "extras", "skip", f"{marks} — optional: pip install 'sempipe[{first_missing}]'"
    )


def _check_completions(env: Mapping[str, str]) -> CheckResult:
    shell = Path(env.get("SHELL", "")).name
    if shell == "fish":
        candidate = Path("~/.config/fish/completions/sempipe.fish").expanduser()
        if candidate.exists():
            return CheckResult("terminal", "ok", "completions installed for fish")
        return CheckResult("terminal", "skip", "no fish completions — optional: see install docs")
    rc_name = _RC_FILES.get(shell)
    if rc_name is None:
        return CheckResult("terminal", "skip", "unknown shell — completions not checked")
    rc_path = Path(rc_name).expanduser()
    try:
        installed = "_SEMPIPE_COMPLETE" in rc_path.read_text(encoding="utf-8")
    except OSError:
        installed = False
    if installed:
        return CheckResult("terminal", "ok", f"completions installed for {shell}")
    return CheckResult("terminal", "skip", f"no {shell} completions — optional: see install docs")
