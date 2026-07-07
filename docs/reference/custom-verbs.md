# Custom verbs - the contract

Two ways to add a verb to smartpipe; built-ins always win over both.

## Named `.sem` verbs (no code)

Any `.sem` file (single stage or a whole pipeline) in
`~/.config/smartpipe/verbs/` becomes a command named after the file:

```console
$ cat ~/.config/smartpipe/verbs/triage.sem
[stage.hot]
verb = "where"
predicate = 'text has "ERROR"'

[stage.themes]
verb = "cluster"
top = 8

$ cat week.log \
    | smartpipe triage
```

Full key validation applies (typos are loud), the file is shareable and
reviewable, and it shows up in `smartpipe --help`. This is the recommended
path: a custom verb that is *data*, not code.

## Python plugins (the Protocol)

A package exposes a `click.Command` through an entry point in the
`smartpipe.verbs` group:

```toml
# pyproject.toml of your plugin package
[project.entry-points."smartpipe.verbs"]
redact = "my_pkg.cli:redact_command"
```

The contract:

- The entry point resolves to a **`click.Command`** - that's the whole
  interface.
- **stdout is sacred**: results only; diagnostics to stderr.
- Exit codes follow smartpipe's table (0 ok, 64 usage, …).
- Never name a built-in - built-ins win, silently.
- A plugin that fails to import (or isn't a Command) is **warned and
  skipped**: your bug never takes down the CLI.

Discovery is lazy: built-in invocations never pay for plugin scanning.
