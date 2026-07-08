# How smartpipe compares

There are a lot of "LLM in your terminal" tools. This page maps where smartpipe fits, and where you might reach for something else.

## Positioning

smartpipe brings semantic operators to Unix pipes: `map`, `filter`, `reduce`,
`cluster`, `join`, and `distinct`.

It works over text and files: PDFs with figures, scans, images, audio, and video.
It can use local Ollama models, or cloud providers you configure explicitly.

The landscape splits into two worlds, and smartpipe sits between the two:

- **Semantic data frameworks** (DocETL, LOTUS) have the exact operator set - map,
  filter, reduce, rank, group-by, automatic chunking. But you express pipelines in
  YAML or a pandas-style Python API and run them with a job runner. They batch-process
  files; they aren't `stdin`→`stdout` filters.
- **Terminal LLM tools** (`llm`, `smartcat`, `mods`, `aichat`, `fabric`, `sgpt`) are
  genuinely pipe-composable and often support local models. Almost all expose a
  single "prompt" verb rather than distinct operators.

smartpipe combines the operator model from the first world with the pipe ergonomics
from the second.

## At a glance

| | Distinct verbs (map/filter/reduce/embed/rank) | stdin→stdout Unix filter | Local model path | Auto file parsing | Terminal-adaptive output |
|---|:---:|:---:|:---:|:---:|:---:|
| **smartpipe** | ✅ all five | ✅ | Ollama autodetect; explicit cloud config | ✅ | ✅ |
| `llm` (Simon Willison) | partial (`embed`/`similar`) | ✅ | via plugin | - | - |
| `smartcat` | one verb | ✅ | ✅ | - | - |
| `mods` | one verb | ✅ | configurable | - | - |
| `fabric` | patterns, not operators | ✅ | ✅ | - | - |
| DocETL / LOTUS | ✅ all | ❌ (YAML/Python) | - | ✅ | ❌ |

## Where the other tools are genuinely better

Some tools fit certain jobs better:

- **`llm`** has a mature plugin ecosystem: many model backends, a logging database,
  and a large community. If you want backend breadth, `llm` is deeper.

- **`fabric`** ships hundreds of curated prompt "patterns." If you want a prompt
  library more than composable verbs, it is a better fit.

- **DocETL / LOTUS** have cost optimizers and a visual pipeline builder. For large
  declarative document jobs with a team, those frameworks are more powerful.
  smartpipe is for the command line, not a data platform.

- **`aichat`** is an all-in-one chat/RAG/session/web UI tool. smartpipe has no chat mode and no server.

## What smartpipe does that's rare or unique

- **Distinct semantic verbs as pipe stages.** smartpipe has `map`, `filter`,
  `reduce`, `embed`, `top_k`, and more. It is not a single "prompt" command.

- **Local model probing, with explicit cloud setup** - if no chat model is configured,
  smartpipe probes local Ollama first. If it finds no usable model, it stops with
  setup instructions. Cloud calls happen only after you choose a cloud model,
  profile, or login path.

- **`grep --color=auto`-style adaptive output** - human view at a terminal, JSONL when piped, with no flag.

- **Automatic recursive chunking in `reduce`** - summarize an input far larger than
  the model's context, with no configuration.

- **Automatic file parsing** - point `--in` at PDFs; you never name a parser.

- **No tool-use surface** - smartpipe doesn't execute model output; a response is treated as data, not commands (see [privacy](privacy.md)).

## When *not* to use smartpipe

- You want a chat REPL → `aichat`, `llm chat`.
- You want a big library of prewritten prompts → `fabric`.
- You're building a large declarative document pipeline with joins and a UI → DocETL.
- You need a model backend smartpipe doesn't support and can't reach via the
  OpenAI-compatible endpoint → `llm` with the right plugin.

> The tool landscape moves monthly; this page reflects the survey behind smartpipe's
> design at the time of writing. Corrections welcome.
