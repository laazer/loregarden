"""The repo-root helper finds the checkout from the tree and from mutmut's copy."""

from __future__ import annotations

import re
from pathlib import Path

import pytest
from tests.repo_paths import REPO_ROOT, RepoRootNotFound, find_repo_root


@pytest.fixture
def checkout(tmp_path: Path) -> Path:
    root = tmp_path / "checkout"
    for tests_dir in (root / "server" / "tests", root / "server" / "mutants" / "tests"):
        tests_dir.mkdir(parents=True)
    (root / "lefthook.yml").write_text("", encoding="utf-8")
    return root


@pytest.mark.parametrize(
    "tests_dir",
    ["server/tests", "server/mutants/tests"],
    ids=["tree", "mutmut-copy-one-level-deeper"],
)
def test_root_is_the_same_from_the_tree_and_from_a_deeper_copy(checkout, tests_dir):
    assert find_repo_root(checkout / tests_dir, environ={}) == checkout


def test_a_worktree_dot_git_file_marks_the_root(tmp_path):
    root = tmp_path / "worktree"
    (root / "server" / "tests").mkdir(parents=True)
    (root / ".git").write_text("gitdir: /elsewhere\n", encoding="utf-8")
    assert find_repo_root(root / "server" / "tests", environ={}) == root


def test_the_override_wins_over_the_walk(checkout, tmp_path):
    other = tmp_path / "other"
    other.mkdir()
    (other / "lefthook.yml").write_text("", encoding="utf-8")
    found = find_repo_root(
        checkout / "server" / "tests", environ={"LOREGARDEN_REPO_ROOT": str(other)}
    )
    assert found == other.resolve()


def test_an_override_that_is_not_a_checkout_is_refused(checkout, tmp_path):
    with pytest.raises(RepoRootNotFound, match="lefthook.yml"):
        find_repo_root(checkout, environ={"LOREGARDEN_REPO_ROOT": str(tmp_path / "nowhere")})


def test_no_marker_anywhere_is_refused(tmp_path):
    lonely = tmp_path / "a" / "b"
    lonely.mkdir(parents=True)
    with pytest.raises(RepoRootNotFound):
        find_repo_root(lonely, environ={})


def test_this_suite_resolves_this_checkout():
    assert (REPO_ROOT / "lefthook.yml").is_file()
    assert (REPO_ROOT / "server" / "tests" / "repo_paths.py").is_file()


#: Ways a test file has climbed above server/ by counting directories. Inside
#: mutmut's copy (server/mutants/tests/) each lands on server/ instead of the
#: repo root, and the test reads a file that is not there.
_DEPTH_CLIMBS = re.compile(
    r"__file__\)(?:\.resolve\(\))?\.parents\[[2-9]\]"  # Path(__file__).parents[2]
    r"|__file__\)(?:\.resolve\(\))?(?:\.parent){3,}"  # .parent.parent.parent
    r"|\b\w*SERVER\w*\.parent\b"  # SERVER_ROOT.parent
    r"|\bserver_(?:root|dir)\.parent\b",
    re.IGNORECASE,
)


def test_no_test_finds_the_repo_root_by_depth():
    tests_dir = REPO_ROOT / "server" / "tests"
    offenders = [
        f"{path.relative_to(tests_dir)}:{lineno}: {line.strip()}"
        for path in sorted(tests_dir.rglob("*.py"))
        if path.name not in {"repo_paths.py", "test_repo_paths.py"}
        for lineno, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1)
        if _DEPTH_CLIMBS.search(line) and not line.lstrip().startswith(("#", "'", '"'))
    ]
    assert not offenders, "use tests.repo_paths.REPO_ROOT:\n" + "\n".join(offenders)
