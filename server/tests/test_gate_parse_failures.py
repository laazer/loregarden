"""A `.py` the Python gates cannot parse leaves through one channel, in one sentence.

`parse_python_source` is that channel: every Python gate parses through it, so
a file it cannot examine raises `UnexaminableFileError` and the gate fails by
naming the file, never with a traceback and never at exit 0. A NUL byte is the
case that proved the gates disagreed — `ast.parse` raises `ValueError` for one
on CPython <= 3.10 and `SyntaxError` from 3.11, and each gate had its own
answer to each.

The NUL fixtures are written by the tests; no NUL-bearing file is committed.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest import mock

import pytest
from tests.repo_templates import from_template
from tests.test_gate_scope_visibility import (
    DIFF_FILTER_SCRIPTS,
    GIT_SUBPROCESS_GATE,
    PY_ORGANIZATION_GATE,
    PY_SILENT_EXCEPT_GATE,
    REFUSAL_SENTENCE,
    _build_repo,
    _commit_agent_work,
    _function_span_in_subprocess,
    _load_script,
    _out,
    _run_gate,
)

PY_GATES = [
    pytest.param(PY_ORGANIZATION_GATE, id="py-org"),
    pytest.param(PY_SILENT_EXCEPT_GATE, id="py-silent-except"),
    pytest.param(GIT_SUBPROCESS_GATE, id="py-git-subprocess"),
]

#: The same gates by module, for driving `main` in-process under a patched parser.
PY_GATE_MODULES = [
    pytest.param("py_organization_check", id="py-org"),
    pytest.param("py_silent_except_check", id="py-silent-except"),
    pytest.param("py_git_subprocess_check", id="py-git-subprocess"),
]

RELPATH = "src/pkg/new_mod.py"


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    """The scope-visibility repository: `main` plus a checked-out ticket branch."""
    return from_template(tmp_path / "repo", "gate_scope_visibility", _build_repo)


@pytest.mark.parametrize("gate", PY_GATES)
def test_a_nul_byte_names_the_file_rather_than_crashing_the_gate(
    repo: Path,
    gate: list[str],
) -> None:
    """All three gates refuse a NUL-bearing file by name, in the refusal sentence.

    Pinning the outcome rather than the exception is what makes this hold on
    every interpreter: before the NUL check moved into `parse_python_source`,
    3.11 handed the organization gate a `SyntaxError` it reported as a
    "syntax error" finding while its siblings refused the file.
    """
    _commit_agent_work(repo, RELPATH, "x = 1\n\x00\n")

    result = _run_gate(gate, repo, "--base", "main")

    assert result.returncode == 1, _out(result)
    assert "Traceback" not in _out(result), _out(result)
    assert RELPATH in _out(result), _out(result)
    assert REFUSAL_SENTENCE in _out(result), _out(result)
    assert "NUL byte" in _out(result), _out(result)
    assert "passed" not in _out(result), _out(result)


@pytest.mark.parametrize("module_name", PY_GATE_MODULES)
def test_a_value_error_from_the_parser_is_unexaminable_in_every_gate(
    repo: Path,
    module_name: str,
    capsys: pytest.CaptureFixture[str],
) -> None:
    """`ast.parse` raising `ValueError` reaches each gate's `main` as a refusal.

    No interpreter the gates accept raises it: `require_supported_python`
    refuses CPython <= 3.10 before a file is read, so a real 3.10 run exits
    `EX_UNAVAILABLE` and says nothing about this branch. The parser is patched
    to fail the way 3.10 did, so a gate that calls `ast.parse` itself instead of
    `parse_python_source` lets the `ValueError` out of `main` and fails here.
    """
    marker = "# the parser refuses this file"
    _commit_agent_work(repo, RELPATH, f"x = 1\n{marker}\n")
    gate = _load_script(module_name)
    real_parse = ast.parse

    def refuse_marked(source, *args, **kwargs):
        if marker in source:
            raise ValueError("source code string cannot contain null bytes")
        return real_parse(source, *args, **kwargs)

    argv = ["--repo", str(repo), "--scope", "worktree", "--base", "main"]
    with mock.patch("ast.parse", side_effect=refuse_marked):
        code = gate.main(argv)

    out = capsys.readouterr().out
    assert code == 1, out
    assert RELPATH in out, out
    assert REFUSAL_SENTENCE in out, out
    assert "ValueError: source code string cannot contain null bytes" in out, out


def test_parse_python_source_refuses_a_nul_byte_on_any_interpreter() -> None:
    module = _load_script("precommit_git_diff")
    with mock.patch.object(module.ast, "parse") as parse:
        with pytest.raises(module.UnexaminableFileError) as caught:
            module.parse_python_source("x = 1\n\x00\n", Path(RELPATH))
    parse.assert_not_called()
    assert RELPATH in str(caught.value)
    assert REFUSAL_SENTENCE in str(caught.value)


def test_a_parse_failure_that_is_not_a_syntax_error_is_unexaminable() -> None:
    module = _load_script("precommit_git_diff")
    with mock.patch.object(module.ast, "parse", side_effect=ValueError("nope")):
        with pytest.raises(module.UnexaminableFileError) as caught:
            module.parse_python_source("x = 1\n", Path(RELPATH))
    assert REFUSAL_SENTENCE in str(caught.value)


@pytest.mark.parametrize("module", DIFF_FILTER_SCRIPTS)
@pytest.mark.parametrize(
    ("body", "reason"),
    [
        pytest.param("x = 1\n\x00\n", "NUL byte", id="nul-byte"),
        pytest.param("def (:\n", "invalid syntax", id="syntax-error"),
    ],
)
def test_a_diff_filter_announces_a_file_it_cannot_parse(
    module: str, repo: Path, body: str, reason: str
) -> None:
    """The span falls back to the reported line, and says why on stderr.

    A parse failure used to fall back in silence, while a read failure was
    announced: the same "no span" outcome, one of them invisible.
    """
    target = repo / RELPATH
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(body)

    result = _function_span_in_subprocess(module, target, repo)

    assert result.returncode == 0, _out(result)
    assert "(7, 7)" in result.stdout, _out(result)
    assert "could not parse" in result.stderr, _out(result)
    assert reason in result.stderr, _out(result)
