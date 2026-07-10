"""Daily catalog cache + probe capability cache: best-effort, never fatal."""

from __future__ import annotations

from pathlib import Path

from smartpipe.config.picker import ProbeChip
from smartpipe.config.state_cache import (
    catalog_path,
    load_catalog,
    load_probe_chips,
    probe_path,
    record_probe,
    store_catalog,
)


def _env(tmp_path: Path) -> dict[str, str]:
    return {"XDG_STATE_HOME": str(tmp_path)}


# --- catalog cache -----------------------------------------------------------------


def test_catalog_path_is_dated_per_provider(tmp_path: Path) -> None:
    path = catalog_path(_env(tmp_path), "openai", "2026-07-08")
    assert path == tmp_path / "smartpipe" / "catalogs" / "openai-2026-07-08.json"


def test_catalog_roundtrip(tmp_path: Path) -> None:
    path = catalog_path(_env(tmp_path), "openai", "2026-07-08")
    store_catalog(path, ("gpt-5.4-mini", "o4-mini"))
    assert load_catalog(path) == ("gpt-5.4-mini", "o4-mini")


def test_catalog_miss_is_none(tmp_path: Path) -> None:
    assert load_catalog(catalog_path(_env(tmp_path), "openai", "2026-07-08")) is None


def test_corrupt_catalog_reads_as_a_miss(tmp_path: Path) -> None:
    path = catalog_path(_env(tmp_path), "openai", "2026-07-08")
    path.parent.mkdir(parents=True)
    path.write_text("{not json", encoding="utf-8")
    assert load_catalog(path) is None
    path.write_text('{"names": [1, 2]}', encoding="utf-8")
    assert load_catalog(path) is None


def test_store_prunes_the_providers_stale_days(tmp_path: Path) -> None:
    env = _env(tmp_path)
    stale = catalog_path(env, "openai", "2026-07-07")
    store_catalog(stale, ("old",))
    other = catalog_path(env, "mistral", "2026-07-07")
    store_catalog(other, ("kept",))
    fresh = catalog_path(env, "openai", "2026-07-08")
    store_catalog(fresh, ("new",))
    assert not stale.exists()  # yesterday's openai catalog swept
    assert other.exists()  # another provider's file untouched
    assert load_catalog(fresh) == ("new",)


def test_store_catalog_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "smartpipe"
    blocker.parent.mkdir(parents=True, exist_ok=True)
    blocker.write_text("a file where the dir should be", encoding="utf-8")
    store_catalog(blocker / "catalogs" / "openai-2026-07-08.json", ("x",))  # swallowed


# --- probe capability cache -----------------------------------------------------------


def test_probe_roundtrip_and_merge(tmp_path: Path) -> None:
    path = probe_path(_env(tmp_path))
    record_probe(path, "ollama/llava", sees=True, hears=False, now=100.0)
    record_probe(path, "gemini/gemini-3.1-flash-lite", sees=True, hears=True, now=200.0)
    chips = load_probe_chips(path)
    assert chips["ollama/llava"] == ProbeChip(sees=True, hears=False, ts=100.0)
    assert chips["gemini/gemini-3.1-flash-lite"] == ProbeChip(sees=True, hears=True, ts=200.0)


def test_reprobe_overwrites_the_models_entry(tmp_path: Path) -> None:
    path = probe_path(_env(tmp_path))
    record_probe(path, "ollama/llava", sees=False, hears=False, now=100.0)
    record_probe(path, "ollama/llava", sees=True, hears=False, now=300.0)
    assert load_probe_chips(path)["ollama/llava"] == ProbeChip(sees=True, hears=False, ts=300.0)


def test_missing_or_corrupt_probe_cache_means_no_chips(tmp_path: Path) -> None:
    path = probe_path(_env(tmp_path))
    assert load_probe_chips(path) == {}
    path.parent.mkdir(parents=True)
    path.write_text("{broken", encoding="utf-8")
    assert load_probe_chips(path) == {}
    path.write_text('{"models": {"x": {"sees": "yes"}}}', encoding="utf-8")
    assert load_probe_chips(path) == {}  # wrong-typed entries are skipped, not fatal


def test_record_probe_never_raises(tmp_path: Path) -> None:
    blocker = tmp_path / "smartpipe"
    blocker.write_text("a file where the dir should be", encoding="utf-8")
    record_probe(blocker / "probe.json", "x/y", sees=True, hears=True, now=1.0)  # swallowed


def test_store_catalog_sweep_spares_the_embed_twin(tmp_path: Path) -> None:
    from smartpipe.config.state_cache import catalog_path

    env = {"XDG_STATE_HOME": str(tmp_path)}
    embed_twin = catalog_path(env, "openai-embed", "2026-07-09")
    store_catalog(embed_twin, ("text-embedding-3-small",))
    chat = catalog_path(env, "openai", "2026-07-09")
    store_catalog(chat, ("gpt-5.4-mini",))  # must not eat openai-embed-2026-07-09.json
    assert load_catalog(embed_twin) == ("text-embedding-3-small",)
    stale_embed = catalog_path(env, "openai-embed", "2026-07-01")
    store_catalog(stale_embed, ("old",))
    store_catalog(catalog_path(env, "openai-embed", "2026-07-09"), ("new",))
    assert load_catalog(stale_embed) is None  # its own stale days still sweep
    assert load_catalog(chat) == ("gpt-5.4-mini",)


# --- the models.dev registry cache -------------------------------------------------------


def test_registry_roundtrip_and_daily_sweep(tmp_path: Path) -> None:
    from smartpipe.config.picker import RegistryCaps
    from smartpipe.config.state_cache import load_registry, registry_path, store_registry

    env = {"XDG_STATE_HOME": str(tmp_path)}
    stale = registry_path(env, "2026-07-01")
    store_registry(stale, {"openai/gpt-5.4-mini": RegistryCaps(image=True, audio=False)})
    fresh = registry_path(env, "2026-07-09")
    caps = {"gemini/gemini-3.1-flash": RegistryCaps(image=True, audio=True)}
    store_registry(fresh, caps)
    assert load_registry(fresh) == caps
    assert load_registry(stale) is None  # yesterday's snapshot swept


def test_registry_miss_and_junk_are_none(tmp_path: Path) -> None:
    from smartpipe.config.state_cache import load_registry, registry_path

    env = {"XDG_STATE_HOME": str(tmp_path)}
    path = registry_path(env, "2026-07-09")
    assert load_registry(path) is None  # no file
    path.parent.mkdir(parents=True)
    path.write_text("{ not json")
    assert load_registry(path) is None
    path.write_text('{"models": {"x": {"image": "yes", "audio": false}}}')
    loaded = load_registry(path)
    assert loaded == {}  # malformed entries claim nothing


def test_store_registry_never_raises(tmp_path: Path) -> None:
    from smartpipe.config.state_cache import store_registry

    blocker = tmp_path / "flat"
    blocker.write_text("a file where the directory should be")
    store_registry(blocker / "registry" / "models-dev-2026-07-09.json", {})  # swallowed
