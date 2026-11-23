"""The index of every git and gh command the code runs, followed through its wrappers."""

from __future__ import annotations

import importlib.util
import json
import textwrap
from pathlib import Path

import pytest
from loregarden.cli.main import main
from loregarden.services.git_call_index import GitCall, RefWrite, index_git_calls, ref_write_kind
from tests.repo_paths import REPO_ROOT

_GATE = REPO_ROOT / ".lefthook" / "scripts" / "py_ref_writers_check.py"

_CHOKEPOINT = """
import subprocess


def run_git(args, *, cwd=None, **kwargs):
    return subprocess.run(["git", *args], cwd=cwd, **kwargs)


def run_gh(args, *, cwd):
    return None
"""


@pytest.fixture(name="package")
def package_fixture(tmp_path: Path):
    """A `loregarden` package with the real chokepoint's module path; returns a writer."""
    root = tmp_path / "loregarden"
    (root / "services").mkdir(parents=True)
    (root / "__init__.py").write_text("")
    (root / "services" / "__init__.py").write_text("")
    (root / "services" / "git_subprocess.py").write_text(_CHOKEPOINT)

    def write(name: str, source: str) -> Path:
        path = root / "services" / name
        path.write_text(textwrap.dedent(source))
        return root

    return write


def _at(calls: list[GitCall], name: str) -> list[GitCall]:
    return [c for c in calls if c.path.endswith(name)]


def test_a_direct_call_is_listed_where_it_runs(package):
    root = package(
        "direct.py",
        """
        from loregarden.services.git_subprocess import run_git

        def tip(repo, branch):
            return run_git(["rev-parse", f"refs/heads/{branch}"], cwd=repo)
        """,
    )

    [call] = _at(index_git_calls(root), "direct.py")

    assert (call.function, call.via, call.subcommand) == ("tip", "run_git", "rev-parse")
    assert call.argv == ("rev-parse", "refs/heads/…")
    assert call.runs_at == f"{call.path}:{call.line}"
    assert call.ref_write is None
    assert not _at(index_git_calls(root), "git_subprocess.py"), (
        "the chokepoint runs no command of its own"
    )


def test_a_wrapper_is_filled_in_at_each_caller(package):
    root = package(
        "wrapped.py",
        """
        from loregarden.services.git_subprocess import run_git

        def _git(repo, *args):
            return run_git(["-C", str(repo), *args], check=False)

        def move(repo, branch, new, old):
            _git(repo, "update-ref", f"refs/heads/{branch}", new, old)

        def drop(repo, branch):
            _git(repo, "branch", "-D", branch)
        """,
    )

    calls = _at(index_git_calls(root), "wrapped.py")

    by_function = {c.function: c for c in calls}
    assert set(by_function) == {"move", "drop"}, "the wrapper's own template is not a command"
    move, drop = by_function["move"], by_function["drop"]
    assert move.argv == ("-C", "…", "update-ref", "refs/heads/…", "…", "…")
    assert (move.via, move.subcommand, move.ref_write) == (
        "_git",
        "update-ref",
        RefWrite.UPDATE_REF,
    )
    assert drop.ref_write is RefWrite.BRANCH_FORCE
    wrapper_line = "loregarden/services/wrapped.py:5"
    assert move.runs_at == drop.runs_at == wrapper_line


def test_a_wrapper_of_a_wrapper_in_another_module_resolves(package):
    package(
        "low.py",
        """
        from loregarden.services.git_subprocess import run_git

        def _git(repo, *args):
            return run_git(list(args), cwd=repo)

        def rev(repo, ref):
            return _git(repo, "rev-parse", "--verify", ref)
        """,
    )
    root = package(
        "high.py",
        """
        from loregarden.services import low

        def head(repo):
            return low.rev(repo, "HEAD")
        """,
    )

    [call] = _at(index_git_calls(root), "high.py")

    assert call.argv == ("rev-parse", "--verify", "HEAD")
    assert call.via == "rev"
    assert call.runs_at == "loregarden/services/low.py:5"


def test_a_wrapper_nobody_calls_is_listed_itself(package):
    root = package(
        "orphan.py",
        """
        from loregarden.services.git_subprocess import run_git

        def _git(repo, *args):
            return run_git(list(args), cwd=repo)
        """,
    )

    [call] = _at(index_git_calls(root), "orphan.py")

    assert (call.function, call.argv, call.subcommand) == ("_git", ("…*",), "")


def test_a_wrapper_that_calls_itself_settles(package):
    root = package(
        "loop.py",
        """
        from loregarden.services.git_subprocess import run_git

        def _git(repo, *args, retry=False):
            if retry:
                return _git(repo, *args)
            return run_git(list(args), cwd=repo)

        def status(repo):
            return _git(repo, "status")
        """,
    )

    calls = _at(index_git_calls(root), "loop.py")

    assert [(c.function, c.argv) for c in calls] == [("status", ("status",))]


def test_git_global_options_are_not_the_command(package):
    root = package(
        "checkouts.py",
        """
        from loregarden.services.git_subprocess import run_git

        def switch(repo, branch):
            run_git(["-C", repo, "-c", "core.hooksPath=/dev/null", "checkout", branch])

        def reset_to(repo, branch):
            run_git(["checkout", "-B", branch, "main"], cwd=repo)
        """,
    )

    calls = {c.function: c for c in _at(index_git_calls(root), "checkouts.py")}

    assert (calls["switch"].subcommand, calls["switch"].ref_write) == ("checkout", None)
    assert calls["reset_to"].ref_write is RefWrite.CHECKOUT_FORCE


def test_raw_subprocess_counts_only_for_git_and_gh(package):
    root = package(
        "raw.py",
        """
        import subprocess

        def fetch(repo):
            subprocess.run(["git", "fetch", "origin"], cwd=repo)

        def delete_remote(repo, ref):
            subprocess.check_output(["gh", "api", "-X", "DELETE", f"repos/o/r/git/refs/{ref}"])

        def build():
            subprocess.run(["docker", "build", "."])
        """,
    )

    calls = {c.function: c for c in _at(index_git_calls(root), "raw.py")}

    assert set(calls) == {"fetch", "delete_remote"}
    assert calls["fetch"].via == "subprocess.run"
    assert calls["delete_remote"].ref_write is RefWrite.GH_DELETE_REF


def test_tests_are_left_out_unless_asked(package):
    root = package(
        "test_thing.py",
        """
        from loregarden.services.git_subprocess import run_git

        def test_it(repo):
            run_git(["status"], cwd=repo)
        """,
    )

    assert not _at(index_git_calls(root), "test_thing.py")
    assert _at(index_git_calls(root, include_tests=True), "test_thing.py")


def _load_gate():
    spec = importlib.util.spec_from_file_location("py_ref_writers_check", _GATE)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.mark.parametrize(
    "tokens",
    [
        ["update-ref", "refs/heads/x", "a", "b"],
        ["update-ref", "-d", "refs/x"],
        ["branch", "-D", "x"],
        ["branch", "-f", "x", "main"],
        ["branch", "--force", "x"],
        ["branch", "-d", "x"],
        ["branch", "--delete", "x"],
        ["branch", "--list", "x"],
        ["checkout", "-B", "x"],
        ["switch", "-C", "x"],
        ["checkout", "x"],
        ["push", "-f", "origin", "x"],
        ["push", "--force-with-lease=x", "origin", "x"],
        ["push", "origin", "x"],
        ["api", "-X", "DELETE", "repos/o/r/git/refs/heads/x"],
        ["api", "-X", "DELETE", "repos/o/r/issues/1"],
        ["rev-parse", "HEAD"],
    ],
)
def test_a_ref_write_is_what_the_gate_calls_one(tokens):
    """`--writes` and the `py-ref-writers` gate must not disagree about what a ref write is."""
    kind = _load_gate()._ref_write(tokens)

    assert ref_write_kind(tokens) == (RefWrite(kind) if kind else None)


def test_the_real_tree_names_every_branch_ref_writer(capsys):
    """Spot checks against writers the landing-survival enumeration lists by hand."""
    assert main(["git", "calls", "--writes", "--json"]) == 0
    writes = json.loads(capsys.readouterr().out)

    def found(path_suffix: str, kind: RefWrite) -> bool:
        return any(w["runs_at"].startswith(path_suffix) and w["ref_write"] == kind for w in writes)

    assert found("loregarden/services/integration_branch_moves.py", RefWrite.UPDATE_REF)
    assert found("loregarden/services/git_merge_noco.py", RefWrite.UPDATE_REF)
    assert found("loregarden/services/branch_triage_service.py", RefWrite.BRANCH_FORCE)
    assert all(w["path"].startswith("loregarden/") for w in writes)
