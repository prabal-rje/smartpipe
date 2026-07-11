# Installing smartpipe

## macOS - Homebrew

```bash
brew install prabal-rje/tap/smartpipe
```

Upgrades ride `brew upgrade` (the tap tracks PyPI daily), and
`smartpipe update` knows it was brew-installed.

## Linux - the one-liner

```bash
curl -LsSf https://prabal-rje.github.io/smartpipe/install.sh | sh
```

Works on macOS too. The script uses Homebrew when you already have it;
otherwise it sets up [uv](https://docs.astral.sh/uv) and runs
`uv tool install smartpipe-cli`. Either way it ends by checking
`smartpipe --version`, and it tells you exactly which commands it runs.
To pin a version, set `SMARTPIPE_VERSION` first (uv installs only):
`SMARTPIPE_VERSION=1.5.1`. Rerunning the one-liner on a machine that already
has smartpipe is safe - it upgrades the existing install (Homebrew or uv)
instead of failing.

## Windows - PowerShell

```powershell
powershell -ExecutionPolicy Bypass -c "irm https://prabal-rje.github.io/smartpipe/install.ps1 | iex"
```

Prefer to read before you pipe to a shell? The scripts are short:
[install.sh](https://prabal-rje.github.io/smartpipe/install.sh) ·
[install.ps1](https://prabal-rje.github.io/smartpipe/install.ps1).

## Or use your own package manager

smartpipe is a Python package; it needs **Python 3.11 or newer** (not sure?
run `python3 --version`). Pick whichever tool you already use:

```bash
uv tool install smartpipe-cli           # uv
pipx install smartpipe-cli              # pipx - isolated, available everywhere
pip install smartpipe-cli               # plain pip
uv add smartpipe-cli                    # inside a uv project
```

PyPI package: `smartpipe-cli`. The command you type is `smartpipe`:

```bash
smartpipe --version
```

## Staying current

`smartpipe update` upgrades in place - it detects how smartpipe was installed
(Homebrew, uv, pipx, or pip), shows the exact upgrade command, and asks
before running it (`--yes` skips the prompt).

smartpipe also checks PyPI for a newer release at most once a day (in the
background, never delaying your command) and prints one stderr note when a
stable release is ahead of yours. It stays quiet in pipes, in CI, and after
either kill switch: `SMARTPIPE_NO_UPDATE_CHECK=1` for one environment, or
`smartpipe config update-check off` to persist the preference.

## Everything ships in the box

One install gives you everything smartpipe supports - documents (PDF, DOCX,
PPTX, XLSX, HTML, EPUB), video (a static `ffmpeg` is bundled), charts with
`--save`, Claude models, local Whisper transcription, and local embeddings.
There are no optional extras at all - one name installs everything. (If an
old guide tells you `pip install 'smartpipe-cli[something]'`, `pip` will warn
about the unknown extra and install the same complete package anyway.)

Two local models download once on first use and cache on disk: `whisper`
(~75 MB) and the local embedder (~130 MB). Every download is announced.

Supported pythons: **3.11-3.13 for the complete box**. On **Python 3.14**, three components (documents,
`whisper`, local embeddings) wait on upstream wheels (`onnxruntime`, `av`) - the
install works and everything else runs; those paths degrade with clear
messages (`doctor` shows exactly what's waiting), and embeddings fall back
to Ollama's `nomic` model. Python 3.11-3.13 has the complete box today.

## Tab completion

The `smartpipe use` wizard offers to set this up for you; the manual
per-shell lines live in [troubleshooting](troubleshooting.md#installing-tab-completion-by-hand).

## Next

You have smartpipe; now it needs a model to talk to. The
[quickstart](quickstart.md) walks you through that in a minute - a ChatGPT
login, a cloud API key, or a free local model.

Nothing to try it on? `smartpipe demo` downloads 26 MB of practice files -
invoices, reports, photos, recordings, screen sessions, JSONL - into
`./smartpipe-playground` and prints commands to run on them (no model needed
for the download, and the first suggestions are free verbs).
