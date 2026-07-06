# Quickstart

Ten minutes, zero assumptions — including that you know what a "model" is. By the
end you'll have run a real semantic transform over your own text.

## What you're about to build

A one-line pipeline that reads text and rewrites every line through an AI model:

```console
$ cat notes.txt | sempipe map "translate to French"
```

`map` is the verb: it applies your instruction to each line and prints the result.
To make it work you need two things — sempipe (the tool) and a **model** (the AI
that does the thinking). Let's get both.

## 1. Install sempipe

```console
$ pip install sempipe
```

(Details and alternatives — pipx, uv — are in [install.md](install.md).)

## 2. Get a model

A "model" is the AI that reads your instruction and produces the answer. sempipe
doesn't include one; it talks to a model running locally or in the cloud. Pick a
path:

### Path A — local & free (recommended)

[Ollama](https://ollama.com) runs open models on your own machine — no account, no
API key, nothing leaves your computer.

```console
$ # 1. Install Ollama from https://ollama.com
$ # 2. Download a small, capable model (~5 GB):
$ ollama pull qwen3:8b
$ # 3. Tell sempipe to use it:
$ sempipe config model ollama/qwen3:8b
```

### Path B — cloud

If you have an API key (OpenAI, Anthropic, Mistral, Gemini, or OpenRouter), point sempipe at a cloud model.
Cloud models are typically faster and stronger, and cost a small amount per use.

```console
$ sempipe config model gpt-5.4-mini
$ export OPENAI_API_KEY=sk-...           # sempipe never stores your key
```

Either way, `sempipe config` (with no arguments) walks you through this
interactively if you'd rather answer a few questions.

Not sure everything took? `sempipe doctor` checks the whole setup in one
screen — without spending a model call.

## 3. Your first transform

```console
$ echo "hello world" | sempipe map "translate to Spanish"
hola mundo
```

`echo` feeds one line in; `map` transforms it; the result comes out. Try it with a
file:

```console
$ printf "good morning\nthank you\n" | sempipe map "translate to French"
bonjour
merci
```

One line in, one line out — in the same order, always.

## 4. Your first extraction

Put field names in `{braces}` and sempipe asks the model for structured data back,
as JSON:

```console
$ echo "Invoice from Acme Corp, dated 2026-01-15, total $1250" \
    | sempipe map "Extract {vendor, date, total}"
{"vendor": "Acme Corp", "date": "2026-01-15", "total": 1250}
```

Because that's JSON, it composes with `jq`:

```console
$ echo "Invoice from Acme Corp, total $1250" \
    | sempipe map "Extract {vendor, total}" | jq -r .total
1250
```

That's the whole idea: sempipe turns messy text into structured data you can pipe
into the tools you already use.

## Where to next

- [`map` in depth](verbs/map.md) — every option, more examples
- [Structured output](concepts/structured-output.md) — braces vs. a strict `--schema` file
- [Models & providers](concepts/models-and-providers.md) — switching models per command, precedence rules
