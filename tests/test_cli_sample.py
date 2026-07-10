"""Full-stack ``sample --by`` (item 65c): the flag reaches the verb intact."""

from __future__ import annotations

import json

from tests.conftest import RunCli


def test_sample_by_stratifies_through_the_real_cli(run_cli: RunCli) -> None:
    stdin = "".join(
        json.dumps({"label": label, "n": n}) + "\n"
        for label, size in (("a", 80), ("b", 20))
        for n in range(size)
    )
    code, out, err = run_cli(["sample", "10", "--by", "label"], stdin=stdin)
    assert code == 0
    labels = [json.loads(line)["label"] for line in out.splitlines()]
    assert labels.count("a") == 8 and labels.count("b") == 2
    assert "2 strata by 'label'" in err


def test_sample_without_by_keeps_the_pinned_contract(run_cli: RunCli) -> None:
    stdin = "".join(f"row-{n}\n" for n in range(100))
    code, out, err = run_cli(["sample", "10"], stdin=stdin)
    assert code == 0
    assert len(out.splitlines()) == 10
    assert "10 of 100 (seed 0)" in err
