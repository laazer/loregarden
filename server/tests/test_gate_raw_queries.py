"""The gate harness answers three diff questions from one `git diff --raw`.

`precommit_git_diff._RunQueries` derives changed paths (`--name-only
--diff-filter=ACMR`), added paths (`--diff-filter=A`) and submodule pointers from
a single `--raw` call, and asks every other question once per run. These tests
hold each derivation to git's own answer, over a repository built to carry every
kind of change the harness has been burned by: a rename, a delete, a type change,
a submodule bump, a path git must quote, a `-diff` attribute, staged-only,
worktree-only and untracked edits, across every scope a gate resolves to.
"""

from __future__ import annotations

import importlib
import subprocess
import sys
from collections import Counter
from pathlib import Path
from unittest import mock

import pytest
from loregarden.services.git_subprocess import run_git

_SCRIPTS = Path(__file__).resolve().parents[2] / ".lefthook" / "scripts"
if str(_SCRIPTS) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS))
diff = importlib.import_module("precommit_git_diff")

#: Long enough that git's rename detection pairs the two names.
RENAMED_BODY = "".join(f"value_{i} = {i}\n" for i in range(40))


def _git(repo: Path, *args: str) -> str:
    return run_git(
        ["-c", "protocol.file.allow=always", *args],
        cwd=repo,
        check=True,
        capture_output=True,
        text=True,
    ).stdout


def _commit(repo: Path, message: str) -> None:
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", message)


def _init(path: Path) -> Path:
    path.mkdir()
    _git(path, "init", "-q", "-b", "main")
    _git(path, "config", "user.email", "t@example.com")
    _git(path, "config", "user.name", "t")
    return path


@pytest.fixture(name="repo", scope="module")
def repo_fixture(tmp_path_factory) -> Path:
    """Every kind of change, spread across the branch, the index and the worktree."""
    root = tmp_path_factory.mktemp("raw-queries")
    sub = _init(root / "sub")
    (sub / "lib.py").write_text("x = 1\n")
    _commit(sub, "sub base")

    repo = _init(root / "repo")
    (repo / "keep.py").write_text("a = 1\n")
    (repo / "gone.py").write_text("b = 1\n")
    (repo / "rename_me.py").write_text(RENAMED_BODY)
    (repo / "becomes_link.py").write_text("c = 1\n")
    (repo / "dir").mkdir()
    (repo / "dir" / "naïve.py").write_text("d = 1\n")
    (repo / ".gitattributes").write_text("hidden.py -diff\n")
    (repo / "hidden.py").write_text("e = 1\n")
    _git(repo, "submodule", "add", "-q", str(sub), "vendor/sub")
    _commit(repo, "base")

    # The branch's commits: one of every status `--raw` reports.
    _git(repo, "checkout", "-q", "-b", "ticket")
    (repo / "keep.py").write_text("a = 2\n")
    (repo / "gone.py").unlink()
    (repo / "rename_me.py").rename(repo / "renamed.py")
    (repo / "becomes_link.py").unlink()
    (repo / "becomes_link.py").symlink_to("keep.py")
    (repo / "dir" / "ünïcode.py").write_text("f = 1\n")
    (repo / "hidden.py").write_text("e = 2\n")
    (sub / "lib.py").write_text("x = 2\n")
    _commit(sub, "sub moves")
    _git(repo / "vendor" / "sub", "pull", "-q", str(sub), "main")
    _commit(repo, "ticket work")

    # Staged only, unstaged only, and untracked.
    (repo / "staged_new.py").write_text("g = 1\n")
    _git(repo, "add", "staged_new.py")
    (repo / "dir" / "naïve.py").write_text("d = 2\n")
    (repo / "untracked.py").write_text("h = 1\n")
    return repo


def _scopes(repo: Path) -> list[tuple[str, str]]:
    merge_base = _git(repo, "merge-base", "main", "HEAD").strip()
    return [
        (diff.STAGED, "main"),
        (diff.WORKTREE, "main"),
        (diff.BRANCH, "main"),
        (diff.SINCE, merge_base),
    ]


def _name_only(repo: Path, scope: str, base: str, diff_filter: str) -> list[str]:
    out = _git(
        repo,
        "diff",
        *diff._scope_args(scope, base),
        "--name-only",
        f"--diff-filter={diff_filter}",
        "--",
    )
    return diff.decoded_git_paths(out)


def _gitlinks_from_nul_raw(repo: Path, scope: str, base: str) -> list[str]:
    """Submodule paths, read from `-z` output so no quoting is involved."""
    fields = _git(repo, "diff", *diff._scope_args(scope, base), "--raw", "-z", "--").split("\0")
    found, i = set(), 0
    while i < len(fields) - 1:
        meta = fields[i].lstrip(":").split()
        names = 2 if meta[4][:1] in "RC" else 1
        if diff.GITLINK_MODE in meta[:2]:
            found.add(fields[i + names])
        i += 1 + names
    return sorted(found)


def test_the_fixture_carries_every_status_the_derivations_must_handle(repo: Path) -> None:
    """Without these the equivalence below could pass on a diff that has none of them."""
    statuses = {e.status[:1] for e in diff._RunQueries(repo).raw(diff.BRANCH, "main")}
    assert {"A", "D", "M", "R", "T"} <= statuses


@pytest.mark.parametrize("which", range(4), ids=["staged", "worktree", "branch", "since"])
def test_changed_and_added_paths_match_gits_name_only(repo: Path, which: int) -> None:
    scope, base = _scopes(repo)[which]
    queries = diff._RunQueries(repo)

    expected_changed = _name_only(repo, scope, base, "ACMR")
    if scope in diff._UNTRACKED_SCOPES:
        expected_changed += diff.git_untracked_paths(repo)
    assert queries.changed_paths(scope, base) == sorted(set(expected_changed))
    assert sorted(queries.added_paths(scope, base)) == sorted(_name_only(repo, scope, base, "A"))
    assert queries.gitlink_paths(scope, base) == _gitlinks_from_nul_raw(repo, scope, base)


def test_a_quoted_path_and_the_submodule_are_both_seen(repo: Path) -> None:
    queries = diff._RunQueries(repo)
    assert "dir/ünïcode.py" in queries.changed_paths(diff.BRANCH, "main")
    assert queries.gitlink_paths(diff.BRANCH, "main") == ["vendor/sub"]


def test_an_unborn_repository_has_only_untracked_changes(tmp_path: Path) -> None:
    repo = _init(tmp_path / "fresh")
    (repo / "first.py").write_text("x = 1\n")
    queries = diff._RunQueries(repo)
    assert queries.changed_paths(diff.WORKTREE, "main") == ["first.py"]
    assert queries.added_paths(diff.WORKTREE, "main") == []
    assert queries.gitlink_paths(diff.WORKTREE, "main") == []


def test_one_gate_run_asks_each_question_once(repo: Path) -> None:
    """The speed this module bought, held: no repeated question, no `--name-only`."""
    real_run = subprocess.run
    with mock.patch.object(diff.subprocess, "run", wraps=real_run) as spawn:
        run = diff.resolve_gate_scope(
            label="t",
            repo=repo,
            diff_scope=diff.WORKTREE,
            base_ref="main",
            explicit_files=[],
            select=lambda _repo, candidates, _discovered: [
                p for p in candidates if p.suffix == ".py"
            ],
        )
    assert run.files, "the run graded nothing, so it proves nothing"
    asked = Counter(
        tuple(call.args[0][1:]) for call in spawn.call_args_list if call.args[0][0] == "git"
    )
    assert all(count == 1 for count in asked.values()), asked
    assert not [argv for argv in asked if "--name-only" in argv], asked
