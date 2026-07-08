---
name: smartpipe
description: "Use when a shell task needs semantic operations over data or files - extracting structured fields from text/PDFs/images, filtering or deduplicating by meaning, clustering themes, joining datasets semantically, summarizing folders of documents, transcribing/searching audio and video, or building JSONL datasets. Triggers: 'extract fields from', 'dedupe', 'cluster', 'classify these', 'search recordings', 'reconcile two lists', 'label a dataset', bulk-process a folder of PDFs/images/mp3/mp4, or any pipe where grep/jq/awk can't judge MEANING. smartpipe is a Unix CLI whose verbs call LLMs; commands compose with ordinary pipes. Do NOT use for pure-syntax transforms (jq/sed suffice) or when no shell is available."
---

# smartpipe: semantic pipes for your terminal

## Overview

smartpipe verbs are Unix filters that call language models. One law governs
everything: **every item in a pipe is a record** - JSONL when piped (parse
stdout directly; NEVER parse stderr), pretty blocks only at a human TTY.
Plain text lines are records in disguise; files become records at ingestion;
tool metadata rides in reserved `__` fields you must never write yourself.

Costs are real: some verbs are free, some call models per item. Read
[cost-and-reliability](skills/smartpipe/cost-and-reliability.md) before any
run over more than ~100 items.

## Quick reference — route by task

| Task | Do | Details |
|---|---|---|
| Read files/folders/stdin into the pipe | `smartpipe FILE…` or positional args on any verb; `--as file\|lines\|jsonl` | [ingestion](skills/smartpipe/ingestion.md) |
| Extract typed fields | `map`/`extend` with `{braces}`; iterate free with `smartpipe schema` | [extraction](skills/smartpipe/extraction.md) |
| Filter / dedupe / cluster / join / rank by meaning | `filter`, `distinct`, `cluster`, `join`, `top_k` | [recipes](skills/smartpipe/recipes.md) |
| Cut cheaply before paying | `where`, `sample`, `join --on`, `distinct --exact` (all free) | [cost-and-reliability](skills/smartpipe/cost-and-reliability.md) |
| Get results out (files, humans, machines) | `write`, `readable`, JSONL stdout | [output](skills/smartpipe/output.md) |
| Multi-step jobs, ready-made pipelines | composable one-liners per job | [recipes](skills/smartpipe/recipes.md) |

## Non-negotiables (violating these wastes money or corrupts data)

1. **Belt every exploratory run:** `--max-calls N`. Rehearse prompts on
   `smartpipe sample 20` (seeded, same rows every run) before scaling.
2. **Free verbs cut first:** `where 'text has "ERROR"' | filter "…"` - never
   send a model what a predicate can drop.
3. **Quote prompts** (braces and spaces are shell-hostile): double quotes
   outside, single inside.
4. **Parse stdout only.** stderr carries notes/receipts/skips for humans.
5. Exit codes: 0 ok · 1 partial (skips happened) · 2 setup · 3 all failed ·
   64 usage. On 2, run `smartpipe doctor`.

## Setup in one line

`smartpipe config` (interactive) or `SMARTPIPE_MODEL=… ` env; API keys are
env-only. `smartpipe doctor --probe` empirically tests what the configured
models can see/hear/watch (4 tiny paid calls).

Install: `pip install smartpipe-cli`. Docs: https://prabal-rje.github.io/smartpipe
