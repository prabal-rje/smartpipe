---
name: smartpipe
description: "Use when a shell task needs semantic operations over data or files - extracting structured fields from text/PDFs/images, filtering or deduplicating by meaning, clustering themes, joining datasets semantically, summarizing folders of documents, transcribing/searching audio and video, or building JSONL datasets. Triggers: 'extract fields from', 'dedupe', 'cluster', 'classify these', 'search recordings', 'reconcile two lists', 'label a dataset', bulk-process a folder of PDFs/images/mp3/mp4, or any pipe where grep/jq/awk can't judge MEANING. smartpipe is a Unix CLI whose verbs call LLMs; commands compose with ordinary pipes. Do NOT use for pure-syntax transforms (jq/sed suffice) or when no shell is available."
---

# smartpipe: semantic pipes for your terminal

## What it is (read this first)

- smartpipe is a shell command. Each verb is a Unix filter; the paid verbs call a language model.
- Verbs compose with ordinary pipes: `cat rows.jsonl | smartpipe filter "a real bug" | smartpipe extend "Add {severity enum(low, high)}"`.
- **Every item in a pipe is a record.** Piped stdout is one JSON object per line (JSONL); parse it directly. Text-only flows stay plain lines.
- Records carry tool metadata in reserved `__` fields. `__source` says which file/line/page each record came from - use it to cite evidence. Never write `__` fields yourself. `--bare` strips them.
- At a terminal (not piped) output becomes pretty numbered blocks for humans. Never parse that view.

## Route by task

| Task | Do | Details |
|---|---|---|
| Read files/folders/stdin/CSV into the pipe | `smartpipe FILE…` or positional files on any verb; `--as file\|lines\|jsonl\|csv` | [ingestion](skills/smartpipe/ingestion.md) |
| Extract typed fields (incl. dates) | `map`/`extend` with `{braces}`; rehearse free with `smartpipe schema` | [extraction](skills/smartpipe/extraction.md) |
| Filter / dedupe / cluster / join / rank by meaning | `filter`, `distinct`, `cluster`, `join`, `top_k` | [recipes](skills/smartpipe/recipes.md) |
| Map who/what connects across a corpus (entities + cited relationships) | `graph --fast` (free); a focus prompt or `--name-top N` for model-read relations | [recipes](skills/smartpipe/recipes.md) |
| Cut cheaply before paying | `where`, `sample`, `join --on`, `distinct --exact` (all free) | [cost-and-reliability](skills/smartpipe/cost-and-reliability.md) |
| Get results out (machines, files, humans) | JSONL stdout, `write`, `readable` | [output](skills/smartpipe/output.md) |
| Multi-step jobs, ready-made pipelines | composable one-liners per job | [recipes](skills/smartpipe/recipes.md) |

## The 5 rules (breaking one wastes money or corrupts data)

1. **Cap spend.** Put `--max-calls N` on every paid verb until the pipeline is proven. Rehearse prompts on `smartpipe sample 20` (same rows every run) before scaling.
2. **Free cuts first.** Drop rows with `where`/`sample`/`distinct --exact` BEFORE `filter`/`map`. Never pay a model for a row a predicate could drop: `smartpipe where 'text has "ERROR"' < app.log | smartpipe filter "a real outage"`.
3. **Quote every prompt.** Double quotes around the whole prompt, single quotes inside: `smartpipe map "Extract {vendor string}"`. Braces and spaces break unquoted shells.
4. **Parse stdout only.** stderr carries notes, receipts, and skips for humans. The terminal's numbered blocks (`#1`, `#2`, …) are human display, never output.
5. **Read the exit code.** 0 = all good · 1 = partial, some items skipped (stderr says which) · 2 = setup broken, run `smartpipe doctor` · 3 = run stopped, most/all items failed · 64 = your command line is wrong (the message includes the fix).

## Setup

- `smartpipe doctor` - free readiness check (config, models, keys present). Exit 0 = ready.
- Set models with `smartpipe config model NAME` or `SMARTPIPE_MODEL=…`; API keys are env-only, never flags.
- `smartpipe doctor --probe` - 4 tiny PAID calls that test what the configured models can actually see/hear/watch.
- Install: `pip install smartpipe-cli`. Docs: https://prabal-rje.github.io/smartpipe
