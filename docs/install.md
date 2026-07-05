# Installing sempipe

sempipe is a Python package. It needs **Python 3.11 or newer** — that's the only
hard requirement. (Not sure what you have? Run `python3 --version`.)

## The one-liner

```console
$ pip install sempipe
```

That's the whole install. `sempipe` is now a command:

```console
$ sempipe --version
sempipe 0.6.0
```

## Recommended: pipx

If you want sempipe available everywhere without adding it to any project's
dependencies, use [pipx](https://pipx.pypa.io) — it installs command-line tools
into isolated environments:

```console
$ pipx install sempipe
```

## With uv

If you use [uv](https://docs.astral.sh/uv):

```console
$ uv tool install sempipe      # like pipx
# or, inside a project:
$ uv add sempipe
```

## Optional extras

sempipe stays small by default (four dependencies). Features that need heavier
libraries are opt-in, so you only install what you use:

| Extra | Enables | Install |
|---|---|---|
| `anthropic` | Claude models (`claude-*`) | `pip install 'sempipe[anthropic]'` |
| `files` | reading PDF, DOCX, PPTX, XLSX, HTML, EPUB via [`--in`](inputs/files.md) | `pip install 'sempipe[files]'` |
| `audio` | transcribing audio files | `pip install 'sempipe[audio]'` |
| `all` | everything above | `pip install 'sempipe[all]'` |

If you run a command that needs an extra you haven't installed, sempipe tells you
exactly which one and how to get it — you never have to guess.

## Next

You have sempipe; now it needs a model to talk to. The
[quickstart](quickstart.md) walks you through that in a minute, whether you want a
free local model or a cloud one.
