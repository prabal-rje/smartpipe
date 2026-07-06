# Maintenance cadence

Three rhythms, each with its command and its "done when".

## Weekly — live Ollama smoke

`make smoke` against a running local Ollama (CI runs the allowed-failure
scheduled job — early-warning radar for API drift, never a gate).
Done when: the pipeline demo passes or the drift is filed as an issue.

## Monthly — dependency + security pass

```console
$ uv lock --upgrade        # review the diff — majors get a look, not a blind bump
$ uvx pip-audit            # advisories against the lock
```

Done when: the lock diff is reviewed, advisories triaged, CHANGELOG notes any move.

## Quarterly — comparison-page re-read

`docs/comparison.md` re-read against each competitor's changelog (the page's own
footer demands it). Done when: the footer date below is bumped.

Last comparison re-read: 2026-07-05 (page written).

## Live-smoke log

Owner-run, cost-capped (`make live-smoke`, once wired; see
`plan/post-1.1/10-live-validity-runbook.md` for the per-workstream log so far —
baseline + workstreams 01/02/03/05/06 all recorded on 2026-07-05, ~30 paid
calls total, four real bugs caught and fixed).

| Date | HEAD | Result |
|---|---|---|
| 2026-07-05 | daebe69 | 10/10 pass, 0 skipped — all five providers (incl. Voxtral hearing a generated beep: "Sharp, High, Alert") |
