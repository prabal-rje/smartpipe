# Releasing

> **Names (D47):** the PyPI distribution is **`smartpipe-cli`** (plain
> `smartpipe` was rejected as too similar to an existing project). The
> command, import, repo, env vars, and config paths all stay `smartpipe`.
> The trusted publisher on PyPI is registered against `smartpipe-cli`.

> CI economics (owner ruling, 2026-07-07): pushes run ONE Linux job;
> the macOS/Windows matrix runs only as the release publish gate
> (release.yml `verify`) or via manual `workflow_dispatch` — macOS minutes
> bill at 10x Linux.

> Before every tag: walk the MANUAL pass in [`qa/README.md`](qa/README.md)
> end to end (about 20 minutes, fixtures included). The automated gates are
> the primary defense; the human pass is the redundant layer. smartpipe

Two parts: the machine gate, then the human walkthrough. Both green before any tag.

## 1. The machine gate

Run **un-piped**, in order (the current release plan's verification matrix is the
authority — see `plan/post-1.0/09-release-v1.1.0.md`):

```console
$ make gates                       # lint + format + pyright strict + coverage
$ uv run pytest tests/test_signals.py -q   # ×3 consecutive — the flakiness bar
$ rm -rf dist && uv build && uvx twine check dist/*
$ make docs-check                  # once wired: link scan + mkdocs --strict
```

## 2. The human UX walkthrough (ten commands, one look each)

| Type | Look for |
|---|---|
| `smartpipe` | the welcome screen, seven verbs |
| `smartpipe --help` | verbs + run/config/doctor/schema listed |
| `smartpipe doctor` | your real setup, ✓/✗ with fixes, exit code matches |
| `env -i PATH=$PATH smartpipe map "hi" </dev/null` | the no-model screen, exit 2 |
| `SMARTPIPE_MODEL=gpt-4o-mini smartpipe map "hi" </dev/null` (no key) | the key-or-login screen |
| `echo hi \| smartpipe map "Extract {a, b}"` | NDJSON with both fields |
| `smartpipe map "Summarize" --in 'docs/*.md' </dev/null` | file items flow |
| `./extract.sem < fixture` | equals the typed invocation |
| `tail -f /tmp/x \| smartpipe filter "…"` + Ctrl-C | live results, drain summary |
| `yes \| smartpipe map "hi" \| head -1` | one line, exit 141, silence |

## 3. Tag + push

Preconditions: matrix green on the exact HEAD, `git status --porcelain` empty.
`git tag vX.Y.Z && git push origin main --tags` — plain push, never force.

## 4. Homebrew tap

The formula lives in this repo at `packaging/homebrew/smartpipe.rb`; the tap
is `prabal-rje/homebrew-tap` (LIVE since 2026-07-08), which Homebrew clones
by naming convention: `brew install prabal-rje/tap/smartpipe`.

The tap bumps ITSELF: its `.github/workflows/bump.yml` runs daily (plus
manual dispatch), reads the latest STABLE version from
`pypi.org/pypi/smartpipe-cli/json` (`info.version` - rc releases never land
there), downloads the sdist, refreshes `url` + `sha256`, and pushes with the
tap's own token. A new PyPI release reaches `brew upgrade` within a day with
zero owner involvement.

Per release, after the PyPI publish lands:

1. Mirror the same `url`/`sha256` bump into `packaging/homebrew/smartpipe.rb`
   here so the two never drift (or trigger the tap's dispatch and copy its
   result).
2. Spot-check: `brew install prabal-rje/tap/smartpipe` in a clean
   environment (the Linuxbrew Docker container works:
   `docker run --rm homebrew/brew brew install prabal-rje/tap/smartpipe`).

## 5. Install surfaces stay version-agnostic

The one-line installers (`packaging/install/install.sh` / `install.ps1`,
published at the docs-site root by `docs.yml`) and every install command in
the README and `docs/install.md` must either install "latest" with no version
baked in, or carry their own bump automation (the Homebrew formula above is
the one deliberate exception, and it has its ritual). A hardcoded version in
any other install surface goes stale the moment a release lands — check this
before every tag. `SMARTPIPE_VERSION` is the *user-side* pin; it never
belongs inside the scripts.
