"""`first_containing`: which commit on a ref first contained a given commit (928).

`landed_sha` means one thing — **D**: the first commit on `landed_branch` that
contains the ticket's work. That is the merge commit when a merge brought the
branch in, and the branch tip itself when the target fast-forwarded to it. The
git question behind D belongs to `git_merge_noco`, which already owns every
no-checkout ancestry question the landing asks (`rev`, `is_ancestor`,
`merge_preview`); these tests are that helper's contract (spec R3).

Every fixture here builds a real repository and makes real merges. `rev-list
--ancestry-path` is the single call the helper leans on, and its behaviour in
the corner cases differs between git versions, so a stubbed rev-list would
measure the stub rather than git (spec risk R-2).

The criterion this file exists for is R3.5. `rev-list --ancestry-path A..B`
prints nothing for three different reasons: `A` *is* the tip of `B`; `B`
reached `A` with no descendant on the path; and `B` does not contain `A` at
all. The first two are answers, the third is a failure, and they must not be
spelled the same way. A helper that collapsed them into "return `commit`"
would be exactly the bug this ticket fixes with a helper in front of it — and
it would pass every other test in this file.
"""

from __future__ import annotations

import logging
from pathlib import Path

import pytest
from loregarden.services.git_merge_noco import first_containing, is_ancestor, rev
from tests.worktree_helpers import commit_on, git, make_repo

NOCO_LOGGER = "loregarden.services.git_merge_noco"

TARGET = "integration/noco"
BRANCH = "feature/noco"


def _warnings(caplog: pytest.LogCaptureFixture) -> list[str]:
    """Every WARNING-or-worse line this module logged, by message."""
    return [
        record.getMessage()
        for record in caplog.records
        if record.name == NOCO_LOGGER and record.levelno >= logging.WARNING
    ]


def _sha(repo: Path, ref: str) -> str:
    return git(repo, "rev-parse", ref).stdout.strip()


def _merge_by_hand(repo: Path, target: str, source: str) -> str:
    """A real `git merge --no-ff` of `source` into `target`, in a throwaway worktree.

    What a person does when they resolve a landing by hand: the merge commit
    exists in git before any loregarden code looks at it.
    """
    tree = repo.parent / f"merge-{target.replace('/', '-')}"
    git(repo, "worktree", "add", "-q", str(tree), target)
    try:
        git(tree, "merge", "--no-ff", "-q", "-m", f"Merge {source} into {target}", source)
        return _sha(tree, "HEAD")
    finally:
        git(repo, "worktree", "remove", "--force", str(tree))


@pytest.fixture(name="repo")
def repo_fixture(tmp_path: Path) -> Path:
    return make_repo(tmp_path)


@pytest.fixture(name="diverged")
def diverged_fixture(repo: Path) -> tuple[Path, str, str]:
    """`BRANCH` and `TARGET` both off `main`, each with a commit of its own.

    Neither contains the other, which is the shape R3.5 is about.
    """
    git(repo, "branch", TARGET, "main")
    git(repo, "branch", BRANCH, TARGET)
    branch_tip = commit_on(repo, BRANCH, "branch.txt", "branch work\n")
    commit_on(repo, TARGET, "target.txt", "target work\n")
    return repo, branch_tip, _sha(repo, TARGET)


@pytest.fixture(name="merged")
def merged_fixture(repo: Path) -> tuple[Path, str, str]:
    """`BRANCH` brought onto `TARGET` by a real merge commit, which `TARGET` then outgrows.

    Returns the repository, the branch's tip, and the merge commit — the one
    answer D names for this graph.
    """
    git(repo, "branch", TARGET, "main")
    git(repo, "branch", BRANCH, TARGET)
    branch_tip = commit_on(repo, BRANCH, "branch.txt", "branch work\n")
    merge = _merge_by_hand(repo, TARGET, BRANCH)
    return repo, branch_tip, merge


# --- R3.1: the merge case ----------------------------------------------------


def test_the_commit_that_first_contained_the_branch_is_the_merge_commit(
    merged: tuple[Path, str, str],
) -> None:
    """R3.1. Two commits land on the target after the merge; neither is the answer."""
    repo, branch_tip, merge = merged
    first = commit_on(repo, TARGET, "c1.txt", "later\n")
    second = commit_on(repo, TARGET, "c2.txt", "later still\n")

    answer = first_containing(repo, branch_tip, f"refs/heads/{TARGET}")

    assert answer == merge, (
        f"the first commit on {TARGET} containing {branch_tip} is the merge commit "
        f"{merge}, not {answer}"
    )
    assert answer not in {first, second, branch_tip}, (
        "the tip of the target and the tip of the branch are the two wrong answers "
        f"this helper exists to stop: got {answer}, tip of target {second}, "
        f"intermediate {first}, tip of branch {branch_tip}"
    )


# --- R3.6: "first", not "any" ------------------------------------------------


def test_a_branch_merged_twice_answers_with_the_first_merge(
    merged: tuple[Path, str, str],
) -> None:
    """R3.6. A second, no-op merge of the same branch does not become the answer.

    git refuses to merge a branch the target already contains, so the second
    merge commit is made with `commit-tree` — the same object a person gets
    from `git merge --no-ff --no-commit` plus a commit, and the only way to put
    one in the graph here.
    """
    repo, branch_tip, first_merge = merged
    target_tip = _sha(repo, TARGET)
    tree = _sha(repo, f"{TARGET}^{{tree}}")
    second_merge = git(
        repo, "commit-tree", tree, "-p", target_tip, "-p", branch_tip, "-m", "re-merge by hand"
    ).stdout.strip()
    git(repo, "update-ref", f"refs/heads/{TARGET}", second_merge)

    answer = first_containing(repo, branch_tip, f"refs/heads/{TARGET}")

    assert answer == first_merge, (
        f"the *first* commit containing {branch_tip} is {first_merge}; the later "
        f"no-op merge {second_merge} contains it too and is not the answer (got {answer})"
    )


# --- R3.2: the fast-forward case ---------------------------------------------


def test_a_fast_forwarded_target_answers_with_the_branch_tip(
    repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R3.2. `rev(T) == rev(B)`: the branch tip is the answer, and it is an answer.

    One of the two legitimate empty `rev-list` ranges. It must come back as a
    sha with nothing logged — not as the '' that means "could not answer".
    """
    git(repo, "branch", TARGET, "main")
    git(repo, "branch", BRANCH, TARGET)
    branch_tip = commit_on(repo, BRANCH, "branch.txt", "branch work\n")
    git(repo, "update-ref", f"refs/heads/{TARGET}", branch_tip)
    assert rev(repo, f"refs/heads/{TARGET}") == branch_tip, "the fixture did not fast-forward"

    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        answer = first_containing(repo, branch_tip, f"refs/heads/{TARGET}")

    assert answer == branch_tip
    assert _warnings(caplog) == [], (
        f"a fast-forwarded target is an answer, not a failure: {_warnings(caplog)}"
    )


# --- R3.3: the input is normalised -------------------------------------------


def test_a_short_sha_is_answered_with_a_full_one(
    repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """R3.3. The commit may arrive abbreviated; what comes back is a full sha.

    `landed_sha` is compared against other shas and handed to `git diff`, so a
    7- or 8-character answer would compare unequal to the same commit.
    """
    git(repo, "branch", TARGET, "main")
    target_tip = _sha(repo, TARGET)

    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        answer = first_containing(repo, target_tip[:8], f"refs/heads/{TARGET}")

    assert answer == target_tip
    assert len(answer) == 40, f"a full sha, not {answer!r}"
    assert _warnings(caplog) == [], (
        f"the commit is the ref tip, which is an answer: {_warnings(caplog)}"
    )


# --- R3.4: an unknown ref is a failure ---------------------------------------


def test_an_unknown_ref_answers_nothing_and_says_so(
    merged: tuple[Path, str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """R3.4. A ref that names nothing cannot be answered about, and it is logged."""
    repo, branch_tip, _ = merged
    missing = "refs/heads/does-not-exist"

    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        answer = first_containing(repo, branch_tip, missing)

    assert answer == "", f"'' means 'could not answer'; got {answer!r}"
    logged = _warnings(caplog)
    assert any("does-not-exist" in line for line in logged), (
        f"the failure must name the ref it could not read: {logged}"
    )


# --- R3.5: the ref does not contain the commit -------------------------------


def test_a_ref_that_does_not_contain_the_commit_answers_nothing_and_says_so(
    diverged: tuple[Path, str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """R3.5. The criterion that separates "could not answer" from "it is the tip".

    `rev-list --ancestry-path B..T` is empty here for the same reason it is
    empty on the fast-forward leg above, and the two must not be answered the
    same way. Returning `commit` here would silently re-introduce the defect
    this ticket fixes: the by-hand landing would record the branch tip again,
    and nothing would say it had failed to find the real one.
    """
    repo, branch_tip, target_tip = diverged
    assert not is_ancestor(repo, branch_tip, target_tip), "the fixture did not diverge"

    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        answer = first_containing(repo, branch_tip, f"refs/heads/{TARGET}")

    assert answer == "", (
        "a target that does not contain the commit has no first-containing commit; "
        f"got {answer!r}, which is {'the commit itself' if answer == branch_tip else 'wrong'}"
    )
    assert answer != branch_tip, "`commit` is never a fallback for a failure (spec risk R-1)"
    logged = _warnings(caplog)
    assert any(branch_tip[:8] in line and TARGET in line for line in logged), (
        f"the failure must name both the commit and the ref: {logged}"
    )


# --- adversarial: inputs and graphs the six criteria do not reach ------------
#
# R3.1-R3.6 pin the shapes the landing makes. What follows is the test-break
# stage's half: the graphs and the inputs that make `rev-list
# --ancestry-path {commit}..{ref}` answer something other than what D means.


def test_a_second_independent_path_answers_with_a_commit_that_first_contained_it(
    repo: Path,
) -> None:
    """Two merges brought the branch in, by routes neither of which contains the other.

    `BRANCH` is merged into a side branch *and* straight into `TARGET`, and
    the side branch is then merged in too. Both of the first two merge commits
    first contained the branch along their own path, and neither is an
    ancestor of the other, so D names no single one — but the third merge,
    which contains the branch only because it contains the other two, is wrong
    under every reading, and so is the tip.

    Measured on git 2.51: `rev-list --ancestry-path` answers one of the two
    minimal merges and `--topo-order` answers the *other*. An implementation
    is free to pick either; it is not free to pick the commit that merely
    inherited the branch, which is what `[0]` or an unconstrained `rev-list`
    would hand back.
    """
    side = "integration/noco-side"
    git(repo, "branch", TARGET, "main")
    git(repo, "branch", side, "main")
    git(repo, "branch", BRANCH, "main")
    branch_tip = commit_on(repo, BRANCH, "branch.txt", "branch work\n")
    into_side = _merge_by_hand(repo, side, BRANCH)
    into_target = _merge_by_hand(repo, TARGET, BRANCH)
    inherited = _merge_by_hand(repo, TARGET, side)

    answer = first_containing(repo, branch_tip, f"refs/heads/{TARGET}")

    assert answer in {into_side, into_target}, (
        "the answer must be a commit that first contained the branch along some "
        f"path: {into_target} (merged straight in) or {into_side} (merged via "
        f"{side}). Got {answer!r}; the commit that merely inherited it is "
        f"{inherited}, and the tip is {_sha(repo, TARGET)}"
    )
    assert is_ancestor(repo, branch_tip, answer), (
        f"whatever is answered must contain the branch: {answer!r}"
    )


def test_an_empty_commit_is_a_failure_and_never_a_revision_range(
    repo: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """`""` is not "the beginning of history" — but git will read it as one.

    `rev-list --ancestry-path ..refs/heads/T` is a *valid* range: the empty
    left side means `HEAD`. So an implementation that interpolates the commit
    straight into the range gets exit 0 and a plausible sha back for a commit
    nobody named. The repository here is built so `HEAD..TARGET` is non-empty,
    which is what makes that mistake visible rather than accidentally right.
    """
    git(repo, "branch", TARGET, "main")
    would_be_answered = commit_on(repo, TARGET, "target.txt", "target work\n")
    assert _sha(repo, "HEAD") != would_be_answered, "the fixture left HEAD on the target"

    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        answer = first_containing(repo, "", f"refs/heads/{TARGET}")

    assert answer == "", (
        "no commit was named, so there is nothing to answer about; a range built "
        f"from '' silently means HEAD.., which answers {would_be_answered}. "
        f"Got {answer!r}"
    )
    assert _warnings(caplog), "a question that could not be asked must be logged"


def test_a_commit_this_repository_does_not_have_answers_nothing_and_says_so(
    merged: tuple[Path, str, str], caplog: pytest.LogCaptureFixture
) -> None:
    """R3.4's sibling: the *commit* is unreadable rather than the ref.

    `land_ticket` hands this function a sha it read from another branch; a
    repository that has since been pruned, or a sha from a different clone,
    makes `rev-list` exit 128 the same way an unknown ref does. The two
    failures must be answered the same way, and neither as a commit.
    """
    repo, _, _ = merged
    absent = "0" * 40

    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        answer = first_containing(repo, absent, f"refs/heads/{TARGET}")

    assert answer == "", f"'' means 'could not answer'; got {answer!r}"
    assert answer != absent, "`commit` is never a fallback for a failure (spec risk R-1)"
    assert _warnings(caplog), "git exited 128 and nothing said so"


def test_a_path_that_is_not_a_repository_answers_nothing_and_says_so(
    tmp_path: Path, caplog: pytest.LogCaptureFixture
) -> None:
    """The workspace root can be wrong; `land_ticket` guards it, this does not.

    Returning the commit here would record a landing in a repository that does
    not exist.
    """
    not_a_repo = tmp_path / "not-a-repo"
    not_a_repo.mkdir()

    with caplog.at_level(logging.WARNING, logger=NOCO_LOGGER):
        answer = first_containing(not_a_repo, "0" * 40, f"refs/heads/{TARGET}")

    assert answer == "", f"'' means 'could not answer'; got {answer!r}"
    assert _warnings(caplog), "a git failure with no log is a landing recorded blind"
