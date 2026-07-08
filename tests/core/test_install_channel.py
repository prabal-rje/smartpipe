"""Install-channel detection: path strings in, channel out — the whole table.

`smartpipe update` must run the upgrade command of the tool that installed it;
guessing wrong would corrupt someone's environment, so every real-world path
shape is pinned here, plus the honest UNKNOWN for anything else.
"""

from __future__ import annotations

import pytest

from smartpipe.core.install_channel import Channel, detect_channel, upgrade_command


@pytest.mark.parametrize(
    ("executable", "module_path", "channel"),
    [
        (  # Homebrew on Apple silicon
            "/opt/homebrew/Cellar/smartpipe/1.4.0/libexec/bin/python3.12",
            "/opt/homebrew/Cellar/smartpipe/1.4.0/libexec/lib/python3.12/site-packages/smartpipe",
            Channel.HOMEBREW,
        ),
        (  # Homebrew on Intel macs
            "/usr/local/Cellar/smartpipe/1.4.0/libexec/bin/python3.12",
            "/usr/local/Cellar/smartpipe/1.4.0/libexec/lib/python3.12/site-packages/smartpipe",
            Channel.HOMEBREW,
        ),
        (  # Homebrew on Linux
            "/home/linuxbrew/.linuxbrew/Cellar/smartpipe/1.4.0/libexec/bin/python3.12",
            "/home/linuxbrew/.linuxbrew/lib/python3.12/site-packages/smartpipe",
            Channel.HOMEBREW,
        ),
        (  # uv tool on Unix
            "/home/u/.local/share/uv/tools/smartpipe-cli/bin/python",
            "/home/u/.local/share/uv/tools/smartpipe-cli/lib/python3.12/site-packages/smartpipe",
            Channel.UV_TOOL,
        ),
        (  # uv tool on Windows — backslashes normalize
            "C:\\Users\\u\\AppData\\Roaming\\uv\\tools\\smartpipe-cli\\Scripts\\python.exe",
            "C:\\Users\\u\\AppData\\Roaming\\uv\\tools\\smartpipe-cli\\Lib\\site-packages\\smartpipe",
            Channel.UV_TOOL,
        ),
        (  # pipx
            "/home/u/.local/pipx/venvs/smartpipe-cli/bin/python",
            "/home/u/.local/pipx/venvs/smartpipe-cli/lib/python3.12/site-packages/smartpipe",
            Channel.PIPX,
        ),
        (  # plain pip into a venv or system python
            "/usr/bin/python3",
            "/usr/lib/python3.12/site-packages/smartpipe",
            Channel.PIP,
        ),
        (  # debian's dist-packages spelling
            "/usr/bin/python3",
            "/usr/lib/python3/dist-packages/smartpipe",
            Channel.PIP,
        ),
        (  # a development checkout (src layout, editable) — honestly unknown
            "/Users/dev/smartpipe/.venv/bin/python",
            "/Users/dev/smartpipe/src/smartpipe",
            Channel.UNKNOWN,
        ),
    ],
)
def test_detect_channel(executable: str, module_path: str, channel: Channel) -> None:
    assert detect_channel(executable, module_path) == channel


def test_specific_markers_outrank_the_pip_fallback() -> None:
    # every managed install ALSO contains site-packages — the manager wins
    path = "/x/pipx/venvs/smartpipe-cli/lib/python3.12/site-packages/smartpipe"
    assert detect_channel("/x/pipx/venvs/smartpipe-cli/bin/python", path) == Channel.PIPX


def test_upgrade_commands_are_the_documented_ones() -> None:
    assert upgrade_command(Channel.HOMEBREW) == ("brew", "upgrade", "smartpipe")
    assert upgrade_command(Channel.UV_TOOL) == ("uv", "tool", "upgrade", "smartpipe-cli")
    assert upgrade_command(Channel.PIPX) == ("pipx", "upgrade", "smartpipe-cli")
    assert upgrade_command(Channel.PIP) == ("pip", "install", "-U", "smartpipe-cli")
    assert upgrade_command(Channel.UNKNOWN) is None
