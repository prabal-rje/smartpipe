"""``smartpipe doctor`` — every no-cost setup check, one screen, exit 0/1 (D18).

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

from smartpipe.config.doctor import CheckResult, doctor_exit_code, render_report
from smartpipe.config.paths import config_path, human_path
from smartpipe.core.errors import ExitCode, SempipeError
from smartpipe.io import tty

if TYPE_CHECKING:
    from collections.abc import Mapping

    from smartpipe.config.store import Config

__all__ = ["doctor_command"]

_KEY_VARS = ("OPENAI_API_KEY", "ANTHROPIC_API_KEY", "MISTRAL_API_KEY")
# D46: nothing is optional — these ship in core; a missing one = broken install
_BUNDLED: tuple[tuple[str, tuple[str, ...]], ...] = (
    ("documents", ("markitdown",)),
    ("whisper", ("faster_whisper",)),
    ("embeddings", ("fastembed",)),
    ("anthropic", ("anthropic",)),
    ("charts", ("plotext", "matplotlib")),  # terminal + --save renderers
    ("ffmpeg", ("imageio_ffmpeg",)),
)

# absent-by-necessity on 3.14 until onnxruntime/av publish wheels (D46)
_WAITING_ON_314_WHEELS = {"documents", "whisper", "embeddings"}
_RC_FILES = {"zsh": "~/.zshrc", "bash": "~/.bashrc"}


@click.command(name="doctor")
@click.option(
    "--probe",
    is_flag=True,
    help="Also send 4 tiny PAID calls to chart which modalities really work.",
)
def doctor_command(probe: bool) -> None:
    """Check that smartpipe is set up and ready — without spending a model call.

    \b
    Verifies: config parses · Ollama reachable · configured models installed ·
    API keys present (never printed) · ChatGPT login · optional extras ·
    shell completions. Exit 0 when everything is green.
    --probe adds the modality matrix: real (tiny) calls, announced first.
    """
    results = asyncio.run(_gather(os.environ))
    click.echo(render_report(results, color=tty.stdout_supports_color()))
    if not probe:
        click.secho(
            "\n⚠ these checks verify SETUP, not ABILITY — run `smartpipe doctor --probe`\n"
            "  to test what your models can actually see and hear (4 tiny paid calls)",
            fg="yellow",
            bold=True,
        )
    if probe:
        from smartpipe.cli.probe_cmd import run_probe

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
    results.append(_check_stt(env, config))
    results.append(_check_keys(env))
    results.append(_check_login(env))
    results.append(_check_extras())
    results.append(_check_completions(env))
    return results


def _check_config(env: Mapping[str, str]) -> tuple[Config | None, CheckResult]:
    from smartpipe.config.store import load_config

    path = config_path(env)
    try:
        config = load_config(path)
    except SempipeError as exc:  # doctor reports sickness, it doesn't die of it
        summary = str(exc).splitlines()[0].removeprefix("error: ")
        return None, CheckResult("config", "fail", f"{human_path(path)}: {summary}")
    if not path.exists():
        return config, CheckResult("config", "skip", "no config file (defaults apply)")
    described = config.model or "no default model"
    if config.fallback_model:
        described += f" · fallback: {config.fallback_model}"
    return config, CheckResult("config", "ok", f"{human_path(path)} parses (model: {described})")


async def _probe(env: Mapping[str, str]) -> tuple[str, ...] | None:
    from smartpipe.models.http_support import make_client
    from smartpipe.models.ollama import ollama_model_names, resolve_host

    async with make_client() as client:
        return await ollama_model_names(client, resolve_host(env))


def _check_ollama(env: Mapping[str, str], names: tuple[str, ...] | None) -> CheckResult:
    from smartpipe.models.ollama import resolve_host

    host = resolve_host(env)
    if names is None:
        return CheckResult(
            "ollama", "fail", f"not reachable at {host} — fix: ollama serve (or set OLLAMA_HOST)"
        )
    return CheckResult("ollama", "ok", f"reachable at {host} ({len(names)} models)")


def _effective_chat(env: Mapping[str, str], config: Config | None) -> str | None:
    return env.get("SMARTPIPE_MODEL", "").strip() or (config.model if config else None)


def _effective_embed(env: Mapping[str, str], config: Config | None) -> str | None:
    configured = env.get("SMARTPIPE_EMBED_MODEL", "").strip() or (
        config.embed_model if config else None
    )
    return configured or "nomic-embed-text"  # the documented default


def _check_model(
    section: str, configured: str | None, names: tuple[str, ...] | None
) -> CheckResult:
    from smartpipe.core.errors import UsageFault
    from smartpipe.models.base import parse_model_ref

    if configured is None:
        return CheckResult(section, "fail", "no model configured — fix: smartpipe use")
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


def _check_stt(env: Mapping[str, str], config: Config | None) -> CheckResult:
    """Where audio transcription would go (D39/05), resolved through the
    SHARED matrix — the same one the container wires at run time, so this
    row can never drift from what actually happens."""
    import sys
    from importlib.util import find_spec

    from smartpipe.core.errors import UsageFault
    from smartpipe.models.base import parse_model_ref
    from smartpipe.models.resolve import resolve_stt

    chat = _effective_chat(env, config)
    provider: str | None = None
    if chat is not None:
        try:
            provider = parse_model_ref(chat).provider
        except UsageFault:
            provider = None  # the chat row reports the parse failure itself
    resolution = resolve_stt(env, config.stt_model if config else None, provider)
    whisper = find_spec("faster_whisper") is not None
    if resolution.kind == "ladder":
        detail = "auto — chat-model hearing, else local whisper (tiny)"
        if not whisper:
            detail += "; whisper unavailable"
        return CheckResult("stt", "skip", detail)
    if resolution.kind == "local":
        if whisper:
            return CheckResult(
                "stt", "ok", f"local whisper ({resolution.source}) — on-device, free"
            )
        if sys.version_info >= (3, 14):
            return CheckResult(
                "stt", "skip", "stt-model local — waiting on upstream Python 3.14 wheels"
            )
        return CheckResult(
            "stt", "fail", "stt-model local but whisper is unavailable — reinstall smartpipe"
        )
    assert resolution.ref is not None
    try:
        ref = parse_model_ref(resolution.ref)
    except UsageFault as exc:
        return CheckResult("stt", "fail", str(exc).splitlines()[0])
    if ref.provider != "openai":
        return CheckResult(
            "stt",
            "fail",
            f"no STT wire for '{ref.provider}' yet — "
            'fix: stt-model = "openai/whisper-1" or "local"',
        )
    if not env.get("OPENAI_API_KEY", "").strip():
        return CheckResult(
            "stt",
            "fail",
            f"{ref} needs OPENAI_API_KEY — "
            'fix: export OPENAI_API_KEY=… (or set stt-model = "local")',
        )
    if resolution.source == "auto":
        return CheckResult(
            "stt",
            "ok",
            f"{ref} (auto: openai chat + OPENAI_API_KEY) — prefer free local? smartpipe use",
        )
    return CheckResult("stt", "ok", f"{ref} — remote transcription ({resolution.source})")


def _check_keys(env: Mapping[str, str]) -> CheckResult:
    parts = (f"{var} {'set' if env.get(var, '').strip() else 'not set'}" for var in _KEY_VARS)
    return CheckResult("keys", "skip", " · ".join(parts))


def _check_login(env: Mapping[str, str]) -> CheckResult:
    from smartpipe.config.credentials import credentials_path, load_oauth

    if load_oauth(credentials_path(env), "openai") is not None:
        return CheckResult("login", "ok", "ChatGPT login present (refreshes automatically)")
    return CheckResult("login", "skip", "no ChatGPT login — optional: smartpipe auth login")


def _check_extras() -> CheckResult:
    import sys
    from importlib.util import find_spec

    installed = {
        name: all(find_spec(module) is not None for module in modules) for name, modules in _BUNDLED
    }
    if all(installed.values()):
        return CheckResult("extras", "ok", "everything ships in the box: " + " · ".join(installed))
    marks = " · ".join(f"{name} {'✓' if present else '✗'}" for name, present in installed.items())
    missing = {name for name, present in installed.items() if not present}
    if sys.version_info >= (3, 14) and missing <= _WAITING_ON_314_WHEELS:
        return CheckResult("extras", "skip", f"{marks} — waiting on upstream Python 3.14 wheels")
    return CheckResult("extras", "fail", f"{marks} — broken install; reinstall smartpipe")


def _check_completions(env: Mapping[str, str]) -> CheckResult:
    shell = Path(env.get("SHELL", "")).name
    if shell == "fish":
        candidate = Path("~/.config/fish/completions/smartpipe.fish").expanduser()
        if candidate.exists():
            return CheckResult("terminal", "ok", "completions installed for fish")
        return CheckResult("terminal", "skip", "no fish completions — optional: see install docs")
    rc_name = _RC_FILES.get(shell)
    if rc_name is None:
        return CheckResult("terminal", "skip", "unknown shell — completions not checked")
    rc_path = Path(rc_name).expanduser()
    try:
        rc_text = rc_path.read_text(encoding="utf-8")
        installed = "_SMARTPIPE_COMPLETE" in rc_text or "_SMARTPIPE_COMPLETE" in rc_text
    except OSError:
        installed = False
    if installed:
        return CheckResult("terminal", "ok", f"completions installed for {shell}")
    return CheckResult("terminal", "skip", f"no {shell} completions — optional: see install docs")
