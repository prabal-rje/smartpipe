#!/bin/sh
# smartpipe one-line installer (macOS / Linux):
#
#   curl -LsSf https://prabal-rje.github.io/smartpipe/install.sh | sh
#
# Prefers Homebrew when present; otherwise bootstraps uv (astral.sh/uv) and
# installs the smartpipe-cli tool. SMARTPIPE_VERSION=X.Y.Z pins the version
# for uv installs (Homebrew always installs the tap's current formula).
# Rerunning is safe: an existing install is upgraded, never broken.
# POSIX sh on purpose - no bashisms, so any /bin/sh runs it.
set -eu

say() { printf '%s\n' "$*"; }
fail() { printf '%s\n' "$*" >&2; exit 1; }

# uv puts tools in ~/.local/bin; on GitHub Actions, later steps need it on PATH.
ci_path() {
    if [ "${GITHUB_ACTIONS:-}" = true ] && [ -n "${GITHUB_PATH:-}" ]; then
        say "GitHub Actions: adding ~/.local/bin to GITHUB_PATH for later steps"
        printf '%s\n' "${HOME}/.local/bin" >> "${GITHUB_PATH}"
    fi
}

# --- platform notes (warn, never block) --------------------------------------
if [ -f /etc/alpine-release ] || ldd --version 2>&1 | grep -qi musl; then
    say "warning: musl libc detected (Alpine?) - some wheels (onnxruntime, fastembed)"
    say "may lack musl builds. If this fails, use a Debian-based container or Homebrew."
    say "Continuing - uv may still succeed."
fi
if [ "$(uname -s 2>/dev/null || true)" = Darwin ] && [ "$(uname -m 2>/dev/null || true)" = x86_64 ] \
    && [ "$(sysctl -n sysctl.proc_translated 2>/dev/null || true)" = 1 ]; then
    say "note: running under Rosetta - if brew fails, use an arm64 shell (arch -arm64 zsh)"
    say "or reinstall native arm64 Homebrew."
fi

# --- install (or, on a rerun, upgrade in place) -------------------------------
if existing=$(command -v smartpipe 2>/dev/null); then
    link=$(readlink "${existing}" 2>/dev/null || true)
    case "${existing}${link}" in
    *Cellar* | *linuxbrew* | *homebrew*)
        say "smartpipe already installed - upgrading with Homebrew"
        brew upgrade prabal-rje/tap/smartpipe || true
        ;;
    *)
        if command -v uv >/dev/null 2>&1 && uv tool list 2>/dev/null | grep -q '^smartpipe-cli '; then
            say "smartpipe already installed - upgrading with uv"
            if [ -n "${SMARTPIPE_VERSION:-}" ]; then
                # a plain 'uv tool install' refuses to touch an installed tool
                uv tool install --force "smartpipe-cli==${SMARTPIPE_VERSION}"
            else
                uv tool upgrade smartpipe-cli
            fi
            ci_path
        else
            say "smartpipe already installed via ${existing}; use your installer's upgrade"
            say "(pipx upgrade smartpipe-cli / pip install -U smartpipe-cli)"
            exit 0
        fi
        ;;
    esac
elif command -v brew >/dev/null 2>&1; then
    if [ -n "${SMARTPIPE_VERSION:-}" ]; then
        say "note: Homebrew installs the tap's current formula - SMARTPIPE_VERSION only pins uv installs"
    fi
    say "installing with Homebrew: brew install prabal-rje/tap/smartpipe"
    brew install prabal-rje/tap/smartpipe
else
    if ! command -v uv >/dev/null 2>&1; then
        say "no Homebrew here - bootstrapping uv (https://astral.sh/uv)"
        if command -v curl >/dev/null 2>&1; then
            curl -LsSf https://astral.sh/uv/install.sh | sh
        elif command -v wget >/dev/null 2>&1; then
            wget -qO- https://astral.sh/uv/install.sh | sh
        else
            fail "error: fetching uv needs curl or wget - install either one and rerun"
        fi
        # uv lands in ~/.local/bin; make it reachable for the rest of THIS run
        PATH="${HOME}/.local/bin:${PATH}"
        export PATH
    fi
    spec="smartpipe-cli${SMARTPIPE_VERSION:+==${SMARTPIPE_VERSION}}"
    say "installing with uv: uv tool install ${spec}"
    uv tool install "${spec}"
    ci_path
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
