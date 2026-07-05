from __future__ import annotations

import pytest
from hypothesis import given
from hypothesis import strategies as st

from sempipe.io.tty import ColorMode, supports_color


@pytest.mark.parametrize(
    ("is_tty", "env", "expected"),
    [
        (True, {}, True),
        (False, {}, False),
        (True, {"NO_COLOR": "1"}, False),
        (True, {"NO_COLOR": ""}, False),  # any presence disables, per the convention
        (True, {"TERM": "dumb"}, False),
        (True, {"TERM": "xterm-256color"}, True),
        (False, {"TERM": "xterm-256color"}, False),
    ],
)
def test_auto_mode_truth_table(is_tty: bool, env: dict[str, str], expected: bool) -> None:
    assert supports_color(is_tty, mode=ColorMode.AUTO, env=env) is expected


@given(
    is_tty=st.booleans(),
    env=st.dictionaries(st.text(min_size=1), st.text(), max_size=5),
)
def test_always_and_never_are_constant(is_tty: bool, env: dict[str, str]) -> None:
    assert supports_color(is_tty, mode=ColorMode.ALWAYS, env=env) is True
    assert supports_color(is_tty, mode=ColorMode.NEVER, env=env) is False
