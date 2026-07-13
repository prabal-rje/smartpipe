# AGENTS.md — sempipe project instructions

Read this before writing any code in this repo. It governs *how* code is written;
*what* gets built is governed by [`plan/`](plan/README.md) (start there for scope,
contracts, and stage order).

## The design template

**`context/intent-finder/` is the design template for this project.** It is a local-only
reference copy of another project (gitignored — never commit or publish it; if it is
missing on a fresh clone, these bullets still bind). Emulate its *shape*:

- **A composition root wires everything** (`src/containers.py` there): services are
  constructed in one place with their dependencies passed in explicitly. No module
  reaches out for its own dependencies, no globals, no hidden state. In sempipe the
  composition root is the CLI layer — build a typed container/factory there and inject
  downward as `Protocol`-typed parameters.
- **Service-per-concern modules** (`src/services/<provider>/`, `src/core/<concern>/`):
  each external system and each cross-cutting concern lives in its own small module with
  its own types. sempipe mirrors this as `models/<provider>.py`, `io/<concern>.py`, etc.
- **Pipelines are async-generator chains** (`StepCallable → AsyncGenerator` there):
  data flows as typed items through composable async steps. In sempipe, readers, the
  runner, and verbs are `AsyncIterator[...]` transformations end to end.
- **Typed configuration objects** built once at startup from the raw source, then passed
  around frozen — never raw dicts floating through the app.

**The one deliberate departure: NO `returns` library.** intent-finder leans on monads
(`FutureResultE`, `IOResult`, HKT, lenses). sempipe does not, ever. Model the same
railway-oriented ideas with **native Python**:

- Expected per-item outcomes → frozen-dataclass **discriminated unions** dispatched with
  `match` (see `Done | Skipped` in `engine/runner`).
- Expected fatal outcomes → the typed exception taxonomy in `core/errors.py`
  (`UsageFault`/`SetupFault`/`ItemError`/`TooManyFailures`), raised at the edges,
  converted to exit codes in exactly one place.
- Optionality → `T | None` with early returns; narrowing via `TypeGuard`, never `cast`.

## Coding guidelines

Follow `~/.codex/coding_guidelines.md` **heavily** — it is the review standard for every
PR. The load-bearing mandates, restated (the returns/lenses sections of that guide are
superseded by the departure above):

1. **Dependency injection, intent-finder style.** Constructors/functions receive their
   collaborators as parameters typed by `Protocol` (`ChatModel`, `ResultWriter`, …).
   Tests inject fakes; nothing monkeypatches internals it could have been handed.
2. **Async generators are the pipeline idiom.** Producing a sequence over time =
   `AsyncIterator[T]` via `async def … yield`. Compose them; don't accumulate lists
   unless the algorithm inherently needs the whole set (`top_k`, `reduce`).
3. **First-class functions.** Behavior is passed as `Callable` (see `with_retries`'
   injected `sleep`/`rand`/`is_retryable`), specialized with `functools.partial`,
   dispatched via mappings — never string-keyed if/elif ladders.
4. **map/filter/reduce thinking.** Comprehensions, generator expressions, `itertools`,
   and the `operator` module over index-juggling for-loops with `.append`. Describe the
   *what*, not the *how*.
5. **`match` over if/elif chains** for tagged unions and enums, with
   `case _ as unreachable: assert_never(unreachable)` (pragma-excluded) so pyright
   proves exhaustiveness.
6. **Immutability by default.** `@dataclass(frozen=True, slots=True)` for every value
   type; `tuple`/`Mapping` in interfaces; evolve with `dataclasses.replace`.
7. **Strong typing, pyright strict, zero errors.** Full signatures everywhere;
   `from __future__ import annotations` + `__all__` in every module; `TypeVar`/
   `ParamSpec`/`Protocol` where they earn their keep; no `Any` leakage past a boundary
   (see `core/jsontools.py` for the untrusted-JSON pattern); no `cast`.
8. **Pure core, imperative shell.** `engine/` has no I/O, no clocks, no env reads —
   inputs are parameters (see `io/tty.supports_color`). I/O lives in `io/`, `models/`,
   `config/`, `cli/`.
9. **Fail fast, fail loudly.** Specific exceptions; never silence; `raise … from`;
   assertions for internal invariants, typed faults for user-reachable ones.
10. **TDD.** Failing test first, minimal implementation, then the full gate suite.

## Resilience is composed by decorators, never handled by the caller

Cross-cutting robustness (retry, backoff, circuit-breaking, failover, rate and
concurrency limiting) is a *property wrapped around a call*, not a concern the caller
reasons about. Every model/parser method is written as if the network were perfect; the
composition root then wraps it in a stack of first-class combinators, so the verb just
calls a plain function and resilience happens underneath it. This is guideline 3 taken to
its conclusion: behavior is data, and robustness is behavior. No verb should ever branch
on "is the wire down?" or "am I being throttled?" itself.

The shape (native Python, no `returns`, in-process, run-scoped, per the departure above):

```python
# each layer is a decorator factory whose arguments are the DEGREE of protection.
# effects (clock, sleep, rand) are injected so tests drive them with fakes.
resilient_complete = rate_limited(concurrency=4, cooldown=clock)(        # how hard to throttle
    retried(RetryPolicy(attempts=5, cap=30.0), sleep=sleep, rand=rand)(  # how many times
        circuit_broken(breaker, fallback=backup.complete)(              # when to give up, then swap
            primary.complete                                            # the plain function
        )
    )
)
# the verb never sees any of this. it receives `resilient_complete` typed as ChatModel.complete
# and calls it. a tripped breaker engages `backup.complete` automatically; the caller can't tell.
```

Each layer:
- preserves the wrapped signature with `ParamSpec` (`P`), so the decorated callable is a
  drop-in for the undecorated one and pyright sees no difference at the call site;
- takes its knobs as constructor arguments (the "degree") and its collaborators as
  injected `Callable`s (clock/sleep/rand), never reaching for a global;
- composes: stack the layers in whatever order the semantics require, or
  `functools.partial` one preset per wire at the composition root.

**Read `context/intent-finder/src/core/` heavily for reference implementations of exactly
this.** Do not lift them (they carry `returns`, Redis, `tenacity`, `limits`, all banned
here); read them as worked examples of the decorator shape, then re-express in native
Python:

| Concern | intent-finder reference | What to borrow (never the deps) |
|---------|-------------------------|---------------------------------|
| Circuit-break + auto-fallback | `core/circuit_breaker/service.py` | the `circuit_breaker(*, fallback=...)` wrapper: on OPEN status, call `fallback(*args, **kw)` transparently instead of the primary; `record_success`/`record_failure` bracket the guarded call |
| Named protection presets | `core/circuit_breaker/config.py` | typed `Config` objects per tier, built once and passed frozen, instead of magic numbers at call sites |
| Rate / concurrency limiting | `core/rate_limit/services.py` | the `limit(rate, *, identifier_key, weight_count, auto_retry)` decorator factory over a plain `async def`; `ParamSpec`-preserving; degree-as-argument |
| Content-addressed caching | `core/caching/` | a wrap-outermost caching layer keyed by a content hash (sempipe mirror: `models/cache.py`) |
| Cross-call gating | `core/distributed_semaphore/` | admission as a wrapper the caller can't see, not something the callee polls (sempipe's is `models/admission.py`, in-process) |

sempipe already has the ingredients as first-class functions: `with_retries` (injected
`sleep`/`rand`/`is_retryable`), `OutboundCallPolicy.execute(ref, operation)` (the operation
thunk seam), and `make_failover`. The direction is to compose them into one resilient
callable built at the composition root.

## Non-negotiable project contracts (style never overrides these)

- **stdout is sacred** — results only; all diagnostics via `io/diagnostics` to stderr
  (ruff `T20` + boundary grep enforce it).
- Exit codes, screens, and CLI wording are pinned contracts: [`plan/ux.md`](plan/ux.md)
  + golden tests. Change the plan file first, then the code.
- Dependencies (D46, owner): everything ships in core — NO optional extras, ever;
  the pitch is seamless multimodality and an install ladder defeats it. The snapshot
  test still guards against ACCIDENTAL additions (deliberate ones refresh the golden),
  and heavy imports stay function-local so the startup budget holds.
  (The DI *pattern* stays hand-rolled rather than pulling in the
  `dependency-injector` package intent-finder uses.)
- Startup budget < 150 ms: heavy imports stay function-local.
- **Homebrew formula rides releases**: `packaging/homebrew/smartpipe.rb` pins the
  sdist url + sha256 of ONE version — every version bump must update it (or note
  why not) as part of the release ritual, and the tap's auto-bump cron (once the
  tap exists) is the runtime mechanism. A stale formula = a broken `brew install`.

## The gates (run before claiming anything works)

```console
$ make gates    # lint + format check + pyright strict + coverage — un-piped, fail-fast
```

Never pipe a gate command to `tail`/`head` — it swallows the non-zero exit code and a
red suite reads as green. **NEVER chain `make gates` and `git commit` in one shell
command** — with `;` or even `&&`-after-a-tail, the commit runs regardless of red
gates. This has shipped red commits THREE documented times (2026-07-06 ×2,
2026-07-08). The only accepted ritual: run gates as its own command, READ the exit
code, then commit in a separate command. Use `make gates` (or the bare `uv run …` commands). Coverage
≥ 90 % overall (100 % for the pure `engine/` modules), goldens via `make golden`,
`CHANGELOG.md` in the same PR, Conventional Commits.

## The release ritual (every version - added 2026-07-10 after v1.4.0 shipped reporting itself as rc1)

**Version sites - grep for the OUTGOING version first, then bump every hit.**
The 1.4.0 miss: only pyproject + CITATION were bumped from rc2 onward;
`__init__.py` kept "1.4.0rc1", so `--version`, `cite`, and update-check all
lied, and the cite GOLDEN pinned the stale value so gates stayed green.

```console
$ grep -rn "OLD_VERSION" src/ tests/ README.md pyproject.toml CITATION.cff
```

1. `pyproject.toml` `version` (then `uv sync` so uv.lock rides along)
2. `CITATION.cff` `version`
3. `src/smartpipe/__init__.py` `__version__` (feeds `--version`, `cite`, update-check)
4. `tests/golden/cite.bibtex` (the cite golden)
5. `README.md` "How to cite" example (+ the Python badge if the matrix changed)
6. `CHANGELOG.md`: date the `[X.Y.Z]` heading (finals only)

**Then, strictly in order, each step its own command:**

1. `make gates` - read the exit code yourself.
2. Commit `chore(release): vX.Y.Z`; tag `vX.Y.Z`; push main; push the tag.
3. Watch `release.yml` to green (verify matrix, then publish with attestations).
4. Confirm PyPI serves it: `pypi.org/pypi/smartpipe-cli/X.Y.Z/json`.
5. **Stable releases only** (the tap is stable-only; rc's skip this): download
   the sdist, sha256 it YOURSELF, verify against PyPI's digest, pin url+sha256
   in `packaging/homebrew/smartpipe.rb`, gates, commit, push; dispatch
   `bump.yml` on `prabal-rje/homebrew-tap`; verify the tap serves the new pin.
6. If any shipped OUTPUT SHAPE changed this release: check the demo video
   sources and published demo-assets for staleness (re-render recipe +
   narration-wav location: plan/problems.md item 79).
7. Close the books: `TODO.md`, `plan/problems.md` strikes, memory.

## Orientation

- What we're building & why: [`README.md`](README.md) → [`idea.md`](idea.md)
- Scope, architecture, UX contracts, stages: [`plan/README.md`](plan/README.md)
- Live task state: [`TODO.md`](TODO.md)
