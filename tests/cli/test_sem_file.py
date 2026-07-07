"""The `.sem` translator — a public format from day one, so every rule is pinned
(D17): exact argv per verb, sibling schema resolution, the shebang property, and
the full error table (unknown key, missing/forbidden verb, wrong types, broken
TOML, missing prompt, key on the wrong verb).
"""

from __future__ import annotations

from pathlib import Path

import pytest

from smartpipe.cli.sem_file import parse_sem
from smartpipe.core.errors import UsageFault

FIXTURES = Path(__file__).parent.parent / "fixtures" / "sem"


def _write(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "stage.sem"
    path.write_text(body, encoding="utf-8")
    return path


# --- golden translation per verb ---------------------------------------------------


def test_map_fixture_translates_exactly() -> None:
    assert parse_sem(FIXTURES / "extract.sem") == [
        "map",
        "Extract {vendor, total}",
        "--output",
        "csv",
        "--fields",
        "vendor,total",
    ]


def test_filter_fixture_translates_exactly() -> None:
    assert parse_sem(FIXTURES / "filter-urgent.sem") == [
        "filter",
        "mentions a deadline",
        "--model",
        "ollama/qwen3:8b",
        "--not",
    ]


def test_embed_fixture_translates_exactly() -> None:
    assert parse_sem(FIXTURES / "embed.sem") == [
        "embed",
        "--embed-model",
        "nomic-embed-text",
        "--fields",
        "vector",
    ]


def test_top_k_fixture_translates_exactly() -> None:
    assert parse_sem(FIXTURES / "rank.sem") == [
        "top_k",
        "5",
        "--near",
        "billing dispute",
        "--threshold",
        "0.7",
    ]


def test_reduce_fixture_translates_exactly() -> None:
    assert parse_sem(FIXTURES / "trend.sem") == [
        "reduce",
        "Summarize the trend",
        "--window",
        "100",
        "--every",
        "20",
        "--verbose",
    ]


def test_shebang_line_is_legal_toml() -> None:
    # the executable-script property: '#' opens a TOML comment (proven, not assumed)
    assert parse_sem(FIXTURES / "extract.sem")[0] == "map"


# --- path resolution -----------------------------------------------------------------


def test_schema_file_resolves_beside_the_sem_file(tmp_path: Path) -> None:
    (tmp_path / "contract.json").write_text("{}", encoding="utf-8")
    path = _write(tmp_path, 'verb = "map"\nprompt = "x"\nschema-file = "contract.json"\n')
    argv = parse_sem(path)
    schema_arg = argv[argv.index("--schema") + 1]
    assert schema_arg == str((tmp_path / "contract.json").resolve())


def test_in_globs_pass_through_for_cwd_resolution(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "map"\nprompt = "x"\nin = ["docs/*.pdf", "notes/*.md"]\n')
    argv = parse_sem(path)
    assert argv == ["map", "x", "--in", "docs/*.pdf", "--in", "notes/*.md"]


# --- bool flags ------------------------------------------------------------------------


def test_false_bool_emits_nothing(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "filter"\nprompt = "x"\nnot = false\n')
    assert parse_sem(path) == ["filter", "x"]


def test_from_files_true_emits_the_flag(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "embed"\nfrom-files = true\n')
    assert parse_sem(path) == ["embed", "--from-files"]


# --- the error table ---------------------------------------------------------------------


def test_unknown_key_names_it_and_lists_valid_keys(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "map"\npromt = "typo"\nprompt = "x"\n')
    with pytest.raises(UsageFault) as excinfo:
        parse_sem(path)
    message = str(excinfo.value)
    assert message.startswith(f"{path}: unknown key 'promt' — valid keys for map: ")
    assert (
        "concurrency, fields, from-files, in, max-calls, model, output, prompt, "
        "prompt-file, schema-file, schema-from" in message
    )
    assert "unattended" in message  # the why


def test_key_on_the_wrong_verb_is_the_unknown_key_error(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "map"\nprompt = "x"\nwindow = 10\n')
    with pytest.raises(UsageFault, match="unknown key 'window'"):
        parse_sem(path)


def test_missing_verb(tmp_path: Path) -> None:
    path = _write(tmp_path, 'prompt = "x"\n')
    with pytest.raises(UsageFault) as excinfo:
        parse_sem(path)
    assert str(excinfo.value).startswith(
        f"{path}: 'verb' is required (map, extend, filter, where, embed, top_k, reduce, join, "
        "split, distinct, outliers, cluster, diff, summarize, sample, getschema, sort, chart)"
    )


def test_verb_run_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "run"\n')
    with pytest.raises(UsageFault, match="verb 'run' can't run from a script"):
        parse_sem(path)


def test_verb_config_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "config"\n')
    with pytest.raises(UsageFault, match="verb 'config' can't run from a script"):
        parse_sem(path)


def test_unknown_verb_is_rejected(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "mapp"\n')
    with pytest.raises(UsageFault, match="'mapp' isn't one of map, extend, filter"):
        parse_sem(path)


def test_wrong_type_names_key_and_value(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "top_k"\nk = "five"\n')
    with pytest.raises(UsageFault) as excinfo:
        parse_sem(path)
    message = str(excinfo.value)
    assert "'k' should be a whole number" in message
    assert "k = 'five'" in message


def test_bool_is_not_a_valid_int(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "reduce"\nprompt = "x"\nwindow = true\n')
    with pytest.raises(UsageFault, match="'window' should be a whole number"):
        parse_sem(path)


def test_int_is_a_valid_threshold(tmp_path: Path) -> None:
    # TOML users write `threshold = 1`; demanding `1.0` would be pedantry
    path = _write(tmp_path, 'verb = "top_k"\nnear = "q"\nthreshold = 1\n')
    assert parse_sem(path) == ["top_k", "--near", "q", "--threshold", "1"]


def test_broken_toml_reuses_the_syntax_error_shape(tmp_path: Path) -> None:
    path = _write(tmp_path, "verb =\n")
    with pytest.raises(UsageFault) as excinfo:
        parse_sem(path)
    message = str(excinfo.value)
    assert message.startswith(f"{path} has a syntax error")
    assert "line 1" in message


def test_prompt_required_for_map(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "map"\noutput = "csv"\n')
    with pytest.raises(UsageFault, match="map needs a prompt"):
        parse_sem(path)


def test_prompt_required_for_filter_and_reduce(tmp_path: Path) -> None:
    for verb in ("filter", "reduce"):
        path = _write(tmp_path, f'verb = "{verb}"\n')
        with pytest.raises(UsageFault, match=f"{verb} needs a prompt"):
            parse_sem(path)


def test_verb_must_be_a_string(tmp_path: Path) -> None:
    path = _write(tmp_path, "verb = 5\n")
    with pytest.raises(UsageFault, match="'verb' should be a string"):
        parse_sem(path)


def test_fields_must_be_an_array_of_strings(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "map"\nprompt = "x"\nfields = ["a", 5]\n')
    with pytest.raises(UsageFault, match="'fields' should be an array of strings"):
        parse_sem(path)


def test_stream_and_concurrency_translate(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "top_k"\nk = 3\nnear = "q"\nstream = true\nconcurrency = 8\n')
    assert parse_sem(path) == [
        "top_k",
        "3",
        "--near",
        "q",
        "--stream",
        "--concurrency",
        "8",
    ]


def test_max_calls_key_translates(tmp_path: Path) -> None:
    path = _write(tmp_path, 'verb = "map"\nprompt = "x"\nmax-calls = 20\n')
    assert parse_sem(path) == ["map", "x", "--max-calls", "20"]


def test_join_sem_translates_with_sibling_right(tmp_path: Path) -> None:
    (tmp_path / "catalog.jsonl").write_text("x\n", encoding="utf-8")
    path = _write(
        tmp_path,
        'verb = "join"\nprompt = "t {left.text} c {right.name}"\nright = "catalog.jsonl"\nk = 3\n',
    )
    assert parse_sem(path) == [
        "join",
        "t {left.text} c {right.name}",
        "--right",
        str((tmp_path / "catalog.jsonl").resolve()),
        "--k",
        "3",
    ]


def test_prompt_file_key_translates_beside_the_script(tmp_path: Path) -> None:
    (tmp_path / "prompt.md").write_text("do the thing\n", encoding="utf-8")
    path = _write(tmp_path, 'verb = "map"\nprompt-file = "prompt.md"\n')
    assert parse_sem(path) == [
        "map",
        "--prompt-file",
        str((tmp_path / "prompt.md").resolve()),
    ]


# --- pipelines (D38/14) ------------------------------------------------------------


def _pipeline_file(tmp_path: Path, body: str) -> Path:
    path = tmp_path / "pipe.sem"
    path.write_text(body, encoding="utf-8")
    return path


PIPELINE = """\
[stage.hot]
verb = "where"
predicate = 'text has "ERROR"'

[stage.numbers]
verb = "summarize"
expression = "count()"
"""


def test_pipeline_parses_stages_in_order(tmp_path: Path) -> None:
    from smartpipe.cli.sem_file import parse_pipeline

    stages = parse_pipeline(_pipeline_file(tmp_path, PIPELINE))
    assert stages is not None
    assert [stage.name for stage in stages] == ["hot", "numbers"]
    assert stages[0].argv == ("where", 'text has "ERROR"')
    assert stages[1].input_name is None  # default: the previous stage


def test_single_stage_files_return_none(tmp_path: Path) -> None:
    from smartpipe.cli.sem_file import parse_pipeline

    path = tmp_path / "one.sem"
    path.write_text('verb = "where"\npredicate = "total > 1"\n', encoding="utf-8")
    assert parse_pipeline(path) is None


def test_input_must_name_an_earlier_stage(tmp_path: Path) -> None:
    body = PIPELINE.replace('expression = "count()"', 'expression = "count()"\ninput = "later"')
    with pytest.raises(UsageFault, match="EARLIER stage"):
        from smartpipe.cli.sem_file import parse_pipeline

        parse_pipeline(_pipeline_file(tmp_path, body))


def test_mixing_top_level_keys_with_stages_is_a_fault(tmp_path: Path) -> None:
    from smartpipe.cli.sem_file import parse_pipeline

    with pytest.raises(UsageFault, match="pick one shape"):
        parse_pipeline(_pipeline_file(tmp_path, 'verb = "map"\nprompt = "x"\n' + PIPELINE))


def test_stage_keys_are_checked_like_single_stage(tmp_path: Path) -> None:
    from smartpipe.cli.sem_file import parse_pipeline

    body = PIPELINE.replace("predicate = ", "typo = 1\npredicate = ")
    with pytest.raises(UsageFault, match="unknown key 'typo'"):
        parse_pipeline(_pipeline_file(tmp_path, body))
