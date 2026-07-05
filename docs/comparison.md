# How sempipe compares

There are a lot of "LLM in your terminal" tools. Here's an honest map of where
sempipe fits, and where you might reach for something else.

## The one-sentence positioning

**sempipe brings the semantic-operator vocabulary of data frameworks like DocETL —
`map`, `filter`, `reduce`, `embed`, `top_k` — to genuine Unix pipes, local-first.**

The landscape splits into two worlds, and sempipe is the bridge:

- **Semantic data frameworks** (DocETL, LOTUS) have the exact operator set — map,
  filter, reduce, rank, group-by, automatic chunking. But you express pipelines in
  YAML or a pandas-style Python API and run them with a job runner. They batch-process
  files; they aren't stdin→stdout citizens.
- **Terminal LLM tools** (`llm`, `smartcat`, `mods`, `aichat`, `fabric`, `sgpt`) are
  genuinely pipe-composable and often local-first — but almost all expose a single
  "prompt" verb, not distinct operators.

sempipe takes the operator model from the first world and the pipe ergonomics from the
second.

## At a glance

| | Distinct verbs (map/filter/reduce/embed/rank) | stdin→stdout Unix filter | Local-first default | Auto file parsing | TTY-adaptive output |
|---|:---:|:---:|:---:|:---:|:---:|
| **sempipe** | ✅ all five | ✅ | ✅ | ✅ | ✅ |
| `llm` (Simon Willison) | partial (`embed`/`similar`) | ✅ | via plugin | — | — |
| `smartcat` | one verb | ✅ | ✅ | — | — |
| `mods` | one verb | ✅ | configurable | — | — |
| `fabric` | patterns, not operators | ✅ | ✅ | — | — |
| DocETL / LOTUS | ✅ all | ❌ (YAML/Python) | — | ✅ | ❌ |

## Where the other tools are genuinely better

Being honest about this matters more than winning a table:

- **`llm`** has a mature plugin ecosystem (dozens of model backends, a logging
  database, a huge community). If you want breadth of model support and tooling,
  `llm` is deeper. sempipe deliberately does one thing.
- **`fabric`** ships hundreds of curated prompt "patterns." If you want a library of
  ready-made prompts more than a set of composable verbs, it's a better fit.
- **DocETL / LOTUS** have cost optimizers and a visual pipeline builder — and
  LOTUS-style semantic join is now in sempipe too (`join`, embed-block-judge)
  (DocWrangler). For large, complex, declarative document-processing jobs with a team,
  those frameworks are more powerful — sempipe is for the command line, not a data
  platform.
- **`aichat`** is an all-in-one (chat, RAG, sessions, a web UI). sempipe has no chat
  mode and no server by design.

## What sempipe does that's rare or unique

- **Five distinct semantic verbs as pipe stages** — not one "prompt" verb. `filter`
  and `reduce` as first-class Unix filters is uncommon; having all five together is,
  as far as the survey found, new.
- **Local-first as the *default*, with clean cloud opt-in** — sempipe never silently
  calls a paid API.
- **`grep --color=auto`-style adaptive output** — human view at a terminal, NDJSON
  when piped, with no flag.
- **Automatic recursive chunking in `reduce`** — summarize an input far larger than
  the model's context, with no configuration.
- **Automatic file parsing** — point `--in` at PDFs; you never name a parser.
- **No tool-use surface** — sempipe executes nothing a model says, so a poisoned
  input can't make it act (see [privacy](privacy.md)).

## When *not* to use sempipe

- You want a chat REPL → `aichat`, `llm chat`.
- You want a big library of prewritten prompts → `fabric`.
- You're building a large declarative document pipeline with joins and a UI → DocETL.
- You need a model backend sempipe doesn't support and can't reach via the
  OpenAI-compatible endpoint → `llm` with the right plugin.

> The tool landscape moves monthly; this page reflects the survey behind sempipe's
> design at the time of writing. Corrections welcome.
