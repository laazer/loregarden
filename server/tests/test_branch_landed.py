"""`landed_branches` (memoized) answers exactly what `branch_work_has_landed` answers.

The memo reuses a verdict across a base fast-forward when neither the merge-base
nor any byte the second diff reads can have changed. Each step below moves base
in a way that either must keep or must invalidate a memoized verdict, and the
memo is carried across all of them.
"""

import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.services import branch_landed
from loregarden.services.branch_landed import branch_work_has_landed, landed_branches

BRANCHES = ("landed", "pending", "deletes", "renamer", "reverted", "glob", "forked-late")
#: The branches whose file lists hold no glob character, so a memoized verdict can carry.
LITERAL_BRANCHES = ["landed", "pending", "deletes", "renamer", "reverted"]


def _git(repo: Path, *args: str) -> str:
    return subprocess.run(
        ["git", *args], cwd=repo, check=True, capture_output=True, text=True
    ).stdout.strip()


def _commit(repo: Path, message: str, files: dict[str, str | None] | None = None) -> None:
    for name, content in (files or {}).items():
        path = repo / name
        if content is None:
            _git(repo, "rm", "-q", str(path.relative_to(repo)))
            continue
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(content, encoding="utf-8")
        _git(repo, "add", str(path.relative_to(repo)))
    _git(repo, "commit", "-q", "-m", message)


def _branch(repo: Path, name: str, start: str = "main") -> None:
    _git(repo, "checkout", "-q", "-b", name, start)


@pytest.fixture
def repo(tmp_path: Path) -> Path:
    root = tmp_path / "repo"
    root.mkdir()
    _git(root, "init", "-q", "-b", "main")
    _git(root, "config", "user.email", "test@example.com")
    _git(root, "config", "user.name", "Test")
    _commit(root, "init", {"a.txt": "a0\n", "keep.txt": "keep\n", "dir/x.txt": "x\n"})

    _branch(root, "landed")
    _commit(root, "landed", {"a.txt": "a1\n"})
    _branch(root, "pending", "main")
    _commit(root, "pending", {"b.txt": "b\n"})
    _branch(root, "deletes", "main")
    _commit(root, "deletes", {"keep.txt": None})
    _branch(root, "renamer", "main")
    _git(root, "mv", "dir/x.txt", "dir/y.txt")
    _commit(root, "rename")
    # Nets to nothing, so it has landed until base takes its first commit alone.
    _branch(root, "reverted", "main")
    _commit(root, "add r", {"r.txt": "r\n"})
    _commit(root, "drop r", {"r.txt": None})
    _branch(root, "glob", "main")
    (root / "g*.txt").write_text("glob\n", encoding="utf-8")
    _git(root, "add", "--", ":(literal)g*.txt")
    _commit(root, "glob")
    _git(root, "checkout", "-q", "main")
    branch_landed.clear_memo()
    yield root
    branch_landed.clear_memo()


def _fresh(repo: Path) -> set[str]:
    names = [name for name in BRANCHES if _git(repo, "branch", "--list", name)]
    return {name for name in names if branch_work_has_landed(repo, "main", name)}


def _memoized(repo: Path) -> set[str]:
    names = [name for name in BRANCHES if _git(repo, "branch", "--list", name)]
    return landed_branches(repo, "main", names)


def _advance_main(repo: Path, step: str) -> None:
    """Move main one step; each step must keep or must invalidate some memoized verdict."""
    if step == "unrelated commit":
        _commit(repo, step, {"unrelated.txt": "u\n"})
    elif step == "squash-merge landed":
        _commit(repo, step, {"a.txt": "a1\n"})
    elif step == "base deletes keep.txt too":
        _commit(repo, step, {"keep.txt": None})
    elif step == "base edits a.txt again":
        _commit(repo, step, {"a.txt": "a2\n"})
    elif step == "base adds a file the glob pathspec matches":
        _commit(repo, step, {"gz.txt": "z\n"})
    elif step == "merge a branch cut after the memo's base":
        _branch(repo, "forked-late", "main")
        _commit(repo, "forked late", {"late.txt": "late\n"})
        _git(repo, "checkout", "-q", "main")
        _commit(repo, "main moves on", {"other.txt": "o\n"})
        _git(repo, "merge", "-q", "--no-ff", "-m", step, "forked-late")
    elif step == "merge a commit the branch later reverted":
        # Moves `reverted`'s merge-base without touching any file it lists.
        _git(repo, "merge", "-q", "--no-ff", "-m", step, "reverted~1")
    elif step == "base rewound":
        _git(repo, "reset", "-q", "--hard", "HEAD~3")
    else:
        raise AssertionError(step)


STEPS = (
    "unrelated commit",
    "merge a commit the branch later reverted",
    "squash-merge landed",
    "base deletes keep.txt too",
    "base adds a file the glob pathspec matches",
    "merge a branch cut after the memo's base",
    "base edits a.txt again",
    "base rewound",
)


def test_memoized_scan_matches_the_predicate_after_every_base_move(repo: Path):
    seen: list[set[str]] = []
    assert _memoized(repo) == _fresh(repo)
    for step in STEPS:
        _advance_main(repo, step)
        expected = _fresh(repo)
        assert _memoized(repo) == expected, step
        seen.append(expected)
    # The oracle is not vacuous: verdicts flip both ways along the way.
    assert seen[0] == {"reverted"}
    assert seen[1] == set()
    assert seen[2] == {"landed"}
    assert seen[3] == {"landed", "deletes"}
    assert "forked-late" in seen[5]
    assert "landed" not in seen[6]


def test_an_unrelated_base_commit_reuses_every_verdict(repo: Path):
    landed_branches(repo, "main", LITERAL_BRANCHES)
    _advance_main(repo, "unrelated commit")
    with patch.object(branch_landed, "_check", wraps=branch_landed._check) as check:
        assert landed_branches(repo, "main", LITERAL_BRANCHES) == {"reverted"}
    assert check.call_count == 0


def test_a_glob_pathspec_is_always_recomputed(repo: Path):
    landed_branches(repo, "main", ["glob"])
    _advance_main(repo, "unrelated commit")
    with patch.object(branch_landed, "_check", wraps=branch_landed._check) as check:
        landed_branches(repo, "main", ["glob"])
    assert check.call_count == 1


def test_a_base_commit_touching_a_branch_file_recomputes_only_that_branch(repo: Path):
    landed_branches(repo, "main", LITERAL_BRANCHES)
    _advance_main(repo, "squash-merge landed")
    with patch.object(branch_landed, "_check", wraps=branch_landed._check) as check:
        assert landed_branches(repo, "main", LITERAL_BRANCHES) == {"landed", "reverted"}
    assert [call.args[2] for call in check.call_args_list] == [_git(repo, "rev-parse", "landed")]


def test_a_moved_tip_is_recomputed(repo: Path):
    assert landed_branches(repo, "main", ["pending"]) == set()
    _git(repo, "checkout", "-q", "pending")
    _commit(repo, "drop b", {"b.txt": None})
    _git(repo, "checkout", "-q", "main")
    assert landed_branches(repo, "main", ["pending"]) == {"pending"} == _fresh(repo) & {"pending"}
