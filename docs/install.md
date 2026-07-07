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

## Optional extras

smartpipe stays small by default (four dependencies). Features that need heavier
libraries are opt-in, so you only install what you use:

| Extra | Enables | Install |
|---|---|---|
| `anthropic` | Claude models (`claude-*`) | `pip install 'smartpipe[anthropic]'` |
| `files` | reading PDF, DOCX, PPTX, XLSX, HTML, EPUB via [`--in`](inputs/files.md) | `pip install 'smartpipe[files]'` |
| `video` | frames + soundtrack from video files (bundled ffmpeg) | `pip install 'smartpipe[video]'` |
| `charts` | `smartpipe chart --save file.svg` (svgwrite) | `pip install 'smartpipe[charts]'` |
| `all` | everything above |

Local transcription (whisper) and local embeddings (fastembed) ship in the
core install - no extra needed. Their models download once on first use
(~75 MB and ~130 MB) and cache on disk. `pip install 'smartpipe[all]'` |

If you run a command that needs an extra you haven't installed, smartpipe tells you
exactly which one and how to get it - you never have to guess.

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
