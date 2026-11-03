"""A merged result must keep every line the target contributed (lg-milestone-that-1513).

Real commits throughout: the check reads git, so a fake would test the fake.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest
from loregarden.services.merge_preservation import DroppedLine, target_preserved
from tests.worktree_helpers import git


def _commit(repo: Path, parents: list[str], files: dict[str, str], message: str) -> str:
    """A commit holding exactly ``files`` on top of ``parents``, made without a checkout."""
    index = repo / ".git" / f"index-{message}"
    env = {"GIT_INDEX_FILE": str(index), "PATH": "/usr/bin:/bin:/usr/local/bin:/opt/homebrew/bin"}
    env |= {
        "GIT_AUTHOR_NAME": "t",
        "GIT_AUTHOR_EMAIL": "t@t",
        "GIT_COMMITTER_NAME": "t",
        "GIT_COMMITTER_EMAIL": "t@t",
    }

    def run(*args: str, stdin: str | None = None) -> str:
        return subprocess.run(
            ["git", *args],
            cwd=repo,
            env=env,
            input=stdin,
            check=True,
            capture_output=True,
            text=True,
        ).stdout.strip()

    for path, content in files.items():
        blob = run("hash-object", "-w", "--stdin", stdin=content)
        run("update-index", "--add", "--cacheinfo", f"100644,{blob},{path}")
    tree = run("write-tree")
    index.unlink()
    flags = [arg for parent in parents for arg in ("-p", parent)]
    return run("commit-tree", tree, *flags, "-m", message)


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    repo = tmp_path / "repo"
    repo.mkdir()
    git(repo, "init", "-q")
    return repo


@pytest.fixture(name="history")
def history_fixture(repo):
    """base → target (main added lines) and base → source (the branch's work)."""
    base = _commit(repo, [], {"a.txt": "one\ntwo\n", "b.txt": "keep\n"}, "base")
    target = _commit(
        repo, [base], {"a.txt": "one\ntwo\nmain-a\n", "b.txt": "keep\nmain-b\nmain-b\n"}, "target"
    )
    source = _commit(
        repo, [base], {"a.txt": "one\ntwo\n", "b.txt": "keep\n", "c.txt": "w\n"}, "src"
    )
    return base, target, source


def test_a_result_holding_every_target_line_is_preserved(repo, history):
    _, target, source = history
    result = _commit(
        repo,
        [target],
        {"a.txt": "one\ntwo\nmain-a\n", "b.txt": "keep\nmain-b\nmain-b\n", "c.txt": "w\n"},
        "ok",
    )

    report = target_preserved(repo, target_sha=target, source_sha=source, result_sha=result)

    assert report.ok, report.detail


def test_a_line_dropped_from_a_file_that_merged_cleanly_is_lost(repo, history):
    """The prior art looks only at conflicted files; b.txt never conflicted."""
    _, target, source = history
    result = _commit(
        repo, [target], {"a.txt": "one\ntwo\nmain-a\n", "b.txt": "keep\nmain-b\n"}, "dropped"
    )

    report = target_preserved(repo, target_sha=target, source_sha=source, result_sha=result)

    assert not report.ok
    assert [(lost.file, lost.line, lost.missing) for lost in report.lost] == [
        ("b.txt", "main-b", 1)
    ], "the target added main-b twice; once is a loss"


def test_a_line_the_source_deleted_is_its_change_not_a_loss(repo):
    base = _commit(repo, [], {"a.txt": "old\nshared\n"}, "base")
    target = _commit(repo, [base], {"a.txt": "old\nshared\nshared\n"}, "target")
    source = _commit(repo, [base], {"a.txt": "old\n"}, "source")
    result = _commit(repo, [target], {"a.txt": "old\nshared\n"}, "result")

    report = target_preserved(repo, target_sha=target, source_sha=source, result_sha=result)

    assert report.ok, report.detail


def test_only_a_declared_drop_is_waived(repo, history):
    _, target, source = history
    result = _commit(repo, [target], {"a.txt": "one\ntwo\n", "b.txt": "keep\n"}, "dropped")
    declared = [DroppedLine(file="a.txt", line="main-a", reason="superseded by c.txt")]

    report = target_preserved(
        repo, target_sha=target, source_sha=source, result_sha=result, declared=declared
    )

    assert {lost.file for lost in report.lost} == {"b.txt"}


def test_structural_failures_come_first(repo, history):
    _, target, source = history
    wrong_parent = _commit(
        repo,
        [source],
        {"a.txt": "one\ntwo\nmain-a\n", "b.txt": "keep\nmain-b\nmain-b\n"},
        "wrong-parent",
    )
    marker = _commit(
        repo,
        [target],
        {"a.txt": "one\n<<<<<<< ours\ntwo\nmain-a\n", "b.txt": "keep\nmain-b\nmain-b\n"},
        "marker",
    )

    parent_report = target_preserved(
        repo, target_sha=target, source_sha=source, result_sha=wrong_parent
    )
    marker_report = target_preserved(repo, target_sha=target, source_sha=source, result_sha=marker)

    assert not parent_report.ok and parent_report.structural
    assert not marker_report.ok and any("a.txt" in s for s in marker_report.structural)


def test_git_that_cannot_answer_raises_rather_than_passing(repo, history):
    _, target, source = history
    with pytest.raises(RuntimeError):
        target_preserved(repo, target_sha=target, source_sha=source, result_sha="f" * 40)
