# Releasing sempipe

Two parts: the machine gate, then the human walkthrough. Both green before any tag.

## 1. The machine gate

Run **un-piped**, in order (the current release plan's verification matrix is the
authority — see `plan/post-1.0/09-release-v1.1.0.md`):

```console
$ make gates                       # lint + format + pyright strict + coverage
$ uv run pytest tests/test_signals.py -q   # ×3 consecutive — the flakiness bar
$ rm -rf dist && uv build && uvx twine check dist/*
$ make docs-check                  # once wired: link scan + mkdocs --strict
```

## 2. The human UX walkthrough (ten commands, one look each)

| Type | Look for |
|---|---|
| `sempipe` | the welcome screen, seven verbs |
| `sempipe --help` | verbs + run/config/doctor/schema listed |
| `sempipe doctor` | your real setup, ✓/✗ with fixes, exit code matches |
| `env -i PATH=$PATH sempipe map "hi" </dev/null` | the no-model screen, exit 2 |
| `SEMPIPE_MODEL=gpt-4o-mini sempipe map "hi" </dev/null` (no key) | the key-or-login screen |
| `echo hi \| sempipe map "Extract {a, b}"` | NDJSON with both fields |
| `sempipe map "Summarize" --in 'docs/*.md' </dev/null` | file items flow |
| `./extract.sem < fixture` | equals the typed invocation |
| `tail -f /tmp/x \| sempipe filter "…"` + Ctrl-C | live results, drain summary |
| `yes \| sempipe map "hi" \| head -1` | one line, exit 141, silence |

## 3. Tag + push

Preconditions: matrix green on the exact HEAD, `git status --porcelain` empty.
`git tag vX.Y.Z && git push origin main --tags` — plain push, never force.
