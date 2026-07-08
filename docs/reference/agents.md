# smartpipe for AI agents

Agents (Claude Code, Codex, and friends) drive smartpipe well - it is a CLI
with machine-parseable output, disclosed costs, and deterministic free verbs.

The agent-facing skill lives at the repository root -
[SKILL.md](https://github.com/prabal-rje/smartpipe/blob/main/SKILL.md) - as a
lean entry point whose quick-reference table routes into focused reference
files under [skills/smartpipe/](https://github.com/prabal-rje/smartpipe/tree/main/skills/smartpipe)
(ingestion, extraction, cost-and-reliability, output, recipes) that agents
load on demand. Together they cover invocation patterns, the free-vs-paid cost model, structured
extraction, reliability semantics (exit codes, skips, the circuit breaker),
and the machine-output contract (parse stdout JSONL; never parse stderr).

Two rules worth repeating to any agent:

- belt exploratory runs with `--max-calls N`
- cut with free verbs (`where`, `sample`) before paid ones
