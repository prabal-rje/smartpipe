"""The sample verb: reproducible by default, representative, order-kept."""

from __future__ import annotations

import io

import pytest

from sempipe.core.errors import ExitCode, UsageFault
from sempipe.verbs.sample import SampleRequest, run_sample

CORPUS = "".join(f"row-{n}\n" for n in range(100))


def _run(count: int, seed: int = 0, stdin_text: str = CORPUS) -> tuple[str, str]:
    import contextlib

    out = io.StringIO()
    err = io.StringIO()
    with contextlib.redirect_stderr(err):
        code = run_sample(
            SampleRequest(count=count, seed=seed), stdin=io.StringIO(stdin_text), stdout=out
        )
    assert code is ExitCode.OK
    return out.getvalue(), err.getvalue()


def test_same_input_same_seed_same_sample() -> None:
    first, _ = _run(10)
    second, _ = _run(10)
    assert first == second  # the default is reproducible, no flags needed


def test_seed_varies_the_sample() -> None:
    default, _ = _run(10)
    other, _ = _run(10, seed=7)
    assert default != other


def test_output_preserves_input_order() -> None:
    out, _ = _run(10)
    numbers = [int(line.split("-")[1]) for line in out.splitlines()]
    assert numbers == sorted(numbers)


def test_small_input_passes_through_with_note() -> None:
    out, err = _run(20, stdin_text="a\nb\nc\n")
    assert out == "a\nb\nc\n"
    assert "3 rows ≤ 20 — all kept" in err


def test_receipt_names_the_seed() -> None:
    _out, err = _run(10)
    assert "10 of 100 (seed 0)" in err


def test_zero_count_is_a_fault() -> None:
    with pytest.raises(UsageFault, match="positive count"):
        _run(0)
