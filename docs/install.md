# Installing smartpipe

smartpipe is a Python package. It needs **Python 3.11 or newer** - that's the only
hard requirement. (Not sure what you have? Run `python3 --version`.)

## The one-liner

```bash
pip install smartpipe-cli
```

That's the whole install. `smartpipe` is now a command:

```bash
smartpipe --version
# → smartpipe 1.1.0
```

## Recommended: `pipx`

If you want smartpipe available everywhere without adding it to any project's
dependencies, use [`pipx`](https://pipx.pypa.io) - it installs command-line tools
into isolated environments:

```bash
pipx install smartpipe-cli
```

## With `uv`

If you use [`uv`](https://docs.astral.sh/uv):

```bash
uv tool install smartpipe-cli   # like pipx
# or, inside a project:
uv add smartpipe-cli
```

PyPI package: `smartpipe-cli`. The command you type is `smartpipe`.

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

The `smartpipe config` wizard offers to set this up for you; the manual
per-shell lines live in [troubleshooting](troubleshooting.md#installing-tab-completion-by-hand).

## Next

You have smartpipe; now it needs a model to talk to. The
[quickstart](quickstart.md) walks you through that in a minute, whether you want a
free local model or a cloud one.
