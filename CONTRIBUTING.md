# Contributing to smartpipe

Thanks for looking under the hood. This page gets you from clone to green gates in a few
minutes.

## Dev setup

Requires [uv](https://docs.astral.sh/uv/) and Python ≥ 3.11 (uv can fetch one for you).

```console
$ git clone https://github.com/prabal-rje/smartpipe && cd smartpipe
$ uv sync --all-extras          # creates .venv with dev + optional deps
$ uv run smartpipe                # welcome screen = working install
```

## The gates

One command runs exactly what CI runs (lint + format check + strict types + coverage):

```console
$ make gates
```

Individual targets: `make test`, `make lint`, `make fmt`, `make types`, `make cov`,
`make smoke` (build the wheel and run it clean), `make golden` (refresh golden files —
review the diff before committing). `make help` lists them all.

Optional but recommended: `uv run pre-commit install` wires ruff into your commits
(pyright runs in CI either way).

## House rules (the short version)

- **stdout is sacred.** Only `io/writers.py` writes results to stdout; only
  `io/diagnostics.py` / `io/progress.py` write to stderr. That's why `print()` is banned
  (ruff `T20`) — route output through those modules.
- **Frozen dataclasses, `Protocol`s, `match`, full typing.** Pyright strict is the
  arbiter. Pure logic lives in `engine/` (no I/O there — it makes everything testable).
- **TDD.** Write the failing test first; every behavior change lands with its test.
- **Docs ship with features.** A user-visible change updates the matching `docs/` page
  and `CHANGELOG.md` in the same PR.
- **Conventional Commits** (`feat:`, `fix:`, `docs:`, `test:`, `chore:`, `build:`, `ci:`).
- **Error messages are a pinned contract**, enforced by golden tests (`UPDATE_GOLDEN=1 uv run pytest` refreshes them —
  the diff then shows up in your PR for review).

Release ritual: [RELEASING.md](RELEASING.md) · upkeep: [MAINTENANCE.md](MAINTENANCE.md).

## Docs search (Algolia DocSearch)

The site is pre-wired for [DocSearch](https://docsearch.algolia.com/) but ships
with it OFF: `extra.algolia` in `mkdocs.yml` holds three empty strings, and
while any of them is empty `overrides/main.html` renders nothing extra -
mkdocs-material's built-in search keeps working. To activate once the
DocSearch application is approved:

1. Put the credentials Algolia sends into `mkdocs.yml` → `extra.algolia`
   (`app_id`, `index_name`, and the **search-only** public `api_key` - that
   key is safe to commit; never the admin key).
2. `make docs-check` - the strict build must stay green.
3. Open the built site and confirm the DocSearch button replaced the
   built-in search box (the override hides `.md-search` and mounts
   docsearch.js v3 in the header).

Reverting to built-in search is emptying the three values again.

## New dependencies

Core install weight is a feature: the runtime dependency list is `click`, `httpx`,
`jsonschema`, `tomli-w` — a snapshot test guards it. Anything heavier goes behind an
optional extra, and heavy imports stay function-local (startup time is budgeted).

**New heavy import? Function-local or justify.** `--help` must never import
`httpx`, `jsonschema`, `anthropic`, or `markitdown` —
`tests/test_startup_imports.py` is the enforcement (it runs `-X importtime` and
fails on any banned module). `make startup` gives an advisory wall-clock number;
the import test is the deterministic gate.
