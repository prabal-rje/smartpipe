#!/bin/sh
# smartpipe one-line installer (macOS / Linux):
#
#   curl -LsSf https://prabal-rje.github.io/smartpipe/install.sh | sh
#
# Prefers Homebrew when present; otherwise bootstraps uv (astral.sh/uv) and
# installs the smartpipe-cli tool. SMARTPIPE_VERSION=X.Y.Z pins the version
# for uv installs (Homebrew always installs the tap's current formula).
# POSIX sh on purpose - no bashisms, so any /bin/sh runs it.
set -eu

say() { printf '%s\n' "$*"; }

if command -v brew >/dev/null 2>&1; then
    if [ -n "${SMARTPIPE_VERSION:-}" ]; then
        say "note: Homebrew installs the tap's current formula - SMARTPIPE_VERSION only pins uv installs"
    fi
    say "installing with Homebrew: brew install prabal-rje/tap/smartpipe"
    brew install prabal-rje/tap/smartpipe
else
    if ! command -v uv >/dev/null 2>&1; then
        say "no Homebrew here - bootstrapping uv (https://astral.sh/uv)"
        curl -LsSf https://astral.sh/uv/install.sh | sh
        # uv lands in ~/.local/bin; make it reachable for the rest of THIS run
        PATH="${HOME}/.local/bin:${PATH}"
        export PATH
    fi
    spec="smartpipe-cli${SMARTPIPE_VERSION:+==${SMARTPIPE_VERSION}}"
    say "installing with uv: uv tool install ${spec}"
    uv tool install "${spec}"
fi

say ""
if command -v smartpipe >/dev/null 2>&1; then
    say "smartpipe installed:"
    smartpipe --version
    say "get started: smartpipe config"
else
    say "installed, but 'smartpipe' is not on PATH in this shell."
    say "uv installs: run 'uv tool update-shell' (or add ~/.local/bin to PATH),"
    say "then open a new shell and check: smartpipe --version"
fi
