# Installing smartpipe

smartpipe is a Python package. It needs **Python 3.11 or newer** - that's the only
hard requirement. (Not sure what you have? Run `python3 --version`.)

## The one-liner

```console
$ pip install smartpipe
```

That's the whole install. `smartpipe` is now a command:

```console
$ smartpipe --version
smartpipe 1.1.0
```

## Recommended: pipx

If you want smartpipe available everywhere without adding it to any project's
dependencies, use [pipx](https://pipx.pypa.io) - it installs command-line tools
into isolated environments:

```console
$ pipx install smartpipe
```

## With uv

If you use [uv](https://docs.astral.sh/uv):

```console
$ uv tool install smartpipe      # like pipx
# or, inside a project:
$ uv add smartpipe
```

## Everything ships in the box

One install gives you the entire multimodal surface - documents (PDF, DOCX,
PPTX, XLSX, HTML, EPUB), video (a static ffmpeg is bundled), charts with
`--save`, Claude models, local Whisper transcription, and local embeddings.
There are no optional extras to remember; old `pip install 'smartpipe[...]'`
commands still work and simply install the same thing.

Two local models download once on first use and cache on disk: whisper
(~75 MB) and the local embedder (~130 MB). Every download is announced.

Supported pythons: **3.11-3.13 for the complete box**. On **Python 3.14**, three components (documents,
whisper, local embeddings) wait on upstream wheels (onnxruntime, av) - the
install works and everything else runs; those paths degrade with clear
messages (`doctor` shows exactly what's waiting), and embeddings fall back
to Ollama's nomic model. Python 3.11-3.13 has the complete box today.

## Tab completion

One line per shell. Completions cover verbs and flags - and `--model` /
`--embed-model` suggest your configured model plus whatever Ollama has installed
(instantly; if Ollama doesn't answer within 150 ms, you just get no suggestions).

**zsh** - add to `~/.zshrc`:

```console
$ eval "$(_SMARTPIPE_COMPLETE=zsh_source smartpipe)"
```

**bash** (4.4+) - add to `~/.bashrc`:

```console
$ eval "$(_SMARTPIPE_COMPLETE=bash_source smartpipe)"
```

**fish** - write it once to your completions directory:

```console
$ _SMARTPIPE_COMPLETE=fish_source smartpipe > ~/.config/fish/completions/smartpipe.fish
```

(For a faster shell startup, redirect the script to a file and `source` it
instead of `eval`-ing on every new shell.)

## Next

You have smartpipe; now it needs a model to talk to. The
[quickstart](quickstart.md) walks you through that in a minute, whether you want a
free local model or a cloud one.
