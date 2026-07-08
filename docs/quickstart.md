# Quickstart

Ten minutes, zero assumptions - including that you know what a "model" is. By the
end you'll have run a real semantic transform over your own text.

## What you're about to build

A one-line pipeline that reads text and rewrites every line through an AI model:

```console
$ cat notes.txt \
    | smartpipe map "translate to French"
```

`map` is the verb: it applies your instruction to each line and prints the result.
To make it work you need two things - smartpipe (the tool) and a **model** (the AI
that does the thinking). Let's get both.

## 1. Install smartpipe

```console
$ pip install smartpipe-cli
```

(Details and alternatives - `pipx`, `uv` - are in [install.md](install.md).)

## 2. Get a model

A "model" is the AI that reads your instruction and produces the answer. smartpipe
doesn't include one; it talks to a model running locally or in the cloud. Pick a
path:

### Path A - local & free (recommended)

[Ollama](https://ollama.com) runs open models on your own machine - no account, no
API key, and model requests stay on that machine.

```console
$ # 1. Install Ollama from https://ollama.com
$ # 2. Download a small, capable model (~5 GB):
$ ollama pull qwen3:8b
$ # 3. Tell smartpipe to use it:
$ smartpipe config model ollama/qwen3:8b
```

### Path B - cloud

If you have an API key (OpenAI, Anthropic, Mistral, Gemini, or OpenRouter), point smartpipe at a cloud model.
Cloud models are typically faster and stronger, and cost a small amount per use.

```console
$ smartpipe config model gpt-5.4-mini
$ export OPENAI_API_KEY=sk-...           # smartpipe reads the key from this variable, not from a saved file
```

Either way, `smartpipe config` (with no arguments) walks you through this
interactively if you'd rather answer a few questions.

Not sure everything took? `smartpipe doctor` checks the whole setup in one
screen - without spending a model call.

## 3. Your first transform

```console
$ echo "hello world" \
    | smartpipe map "translate to Spanish"
hola mundo
```

`echo` feeds one line in; `map` transforms it; the result comes out. Try it with a
file:

```console
$ printf "good morning\nthank you\n" \
    | smartpipe map "translate to French"
bonjour
merci
```

One line in, one line out, in the same order.

## 4. Your first extraction

Put field names in `{braces}` and smartpipe asks the model for structured data back,
as JSON:

```console
$ echo "Invoice from Acme Corp, dated 2026-01-15, total $1250" \
    | smartpipe map "Extract {vendor, date, total}"
{"vendor": "Acme Corp", "date": "2026-01-15", "total": 1250}
```

Because that's JSON, it composes with `jq` (never met `jq`? one-line intro in
[the Unix toolbox](concepts/pipes-and-items.md#the-unix-toolbox-in-five-lines)):

```console
$ echo "Invoice from Acme Corp, total $1250" \
    | smartpipe map "Extract {vendor, total}" \
    | jq -r .total                 # jq pulls one field out of the JSON
1250
```

That's the whole idea: smartpipe turns messy text into structured data you can pipe
into the tools you already use.

## Where to next


- **Working with files?** `smartpipe map "summarize" --in 'reports/*.pdf'` -
  PDFs, images, audio, and video are first-class ([inputs](inputs/files.md)).
- **Cutting costs?** Put the free verbs first: `smartpipe where 'level == "error"'`
  before any paid stage, `smartpipe sample 20` while iterating, and watch the
  live token/media counts in the status bar ([models & providers](concepts/models-and-providers.md)).
- **Prepping data at scale?** `distinct` to fold near-duplicates, `extend` to
  add judge scores, `summarize`/`chart` for the balance tables - the
  [training-data cookbook](cookbook/training-data-prep.md) walks the whole loop.
- **Same pipeline every week?** Save it as a [multi-stage `.sem` file](reference/sem-files.md)
  or a [custom verb](reference/custom-verbs.md); turn on the
  [result cache](privacy.md) so re-runs stop costing.
