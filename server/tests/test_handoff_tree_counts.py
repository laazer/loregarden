"""How far the handed-over tree is from its target, in the two counts both surfaces report.

Split out of `test_handoff_tree_status.py`, which crossed the 2500-line module
cap: these measure `describe_handoff_tree` and the `PreparedTree` value it takes
— how many commits behind, how many files dirty, and the rule that "it is
current" and "we could not tell" must never collapse into one answer. The
failure *sentence* — what an operator is told when the tree could not be
prepared at all — stays in the original module with the fixtures that compose
it. Nothing here is edited; only moved.

The question it answers for the operator is the one the old response did not:
"can I paste this and trust the tree it will run in?"
"""

from pathlib import Path

import pytest
from tests.worktree_helpers import commit_on, git, make_repo

TARGET = "integration/LG-MS"
TICKET = "lg-milestone-that-917"


def describe(repo: Path, *, target_branch: str = "", ticket_external_id: str = ""):
    """`describe_handoff_tree`, over the one value a handoff boundary hands it.

    AC6 (lg-milestone-that-974): the tree and the branch it was brought up to
    arrive as a single `PreparedTree`, because they are two readings of one
    resolution and a signature that takes them separately is what let a caller
    mix sources.
    """
    from loregarden.services.handoff_tree_status import PreparedTree, describe_handoff_tree

    return describe_handoff_tree(
        PreparedTree(repo_root=repo, target_branch=target_branch),
        ticket_external_id=ticket_external_id,
    )


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    """A repo on `main`, with a target branch that has nothing extra on it yet.

    Built in a fixture rather than in the test bodies: a broken fixture is an
    ERROR, where the same breakage inside a test body reads as a real finding
    about the code under test.
    """
    repo = make_repo(tmp_path)
    git(repo, "branch", TARGET)
    return repo


def _ahead(repo: Path, commits: int) -> None:
    """Put `commits` commits on the target branch that the checkout lacks."""
    for n in range(commits):
        commit_on(repo, TARGET, f"landed-{n}.txt", f"a sibling's work {n}\n")


def _dirty(repo: Path, files: int) -> None:
    for n in range(files):
        (repo / f"in-flight-{n}.txt").write_text("an operator is still writing this\n")


def test_a_tree_that_carries_its_target_has_nothing_to_warn_about(repo):
    """AC10. The success shape: nothing behind, nothing dirty, nothing to say."""
    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    # The model's whole contract, so a dict or a tuple cannot stand in for it:
    # the route and the harness both read these three by name.
    assert set(status.model_dump()) == {"behind", "dirty_files", "warning"}
    assert status.behind == 0
    assert status.dirty_files == 0
    assert status.warning == ""


def test_the_commits_a_tree_is_missing_are_counted(repo):
    """AC10/AC11. `tree_behind` is what the response reports, so it has to be
    the real count and not a boolean dressed as one — 33 of 39 measured trees
    were behind, median 40 commits, max 134."""
    _ahead(repo, 3)

    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.behind == 3
    assert status.dirty_files == 0


def test_uncommitted_files_are_counted_even_when_the_tree_is_current(repo):
    """AC10. `behind == 0` means there was nothing to refresh, so a dirty tree
    is not a degraded handoff — the operator is told nothing, correctly."""
    _dirty(repo, 2)

    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.dirty_files == 2
    assert status.behind == 0
    assert status.warning == "", "a current tree has nothing to warn about, dirty or not"


def test_a_dirty_tree_left_behind_says_so_in_the_operator_s_own_terms(repo):
    """AC11's degraded sentence, verbatim. It has to name the path they will
    paste into, the branch the tree does *not* carry, both counts, and the one
    action that fixes it — a `logger.warning` on the server hands them the
    stale tree this ticket is about and tells them nothing.
    """
    _ahead(repo, 2)
    _dirty(repo, 3)

    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.behind == 2
    assert status.dirty_files == 3
    assert status.warning == (
        f"Commit or stash the 3 uncommitted changes, then copy again: "
        f"it is 2 commits behind {TARGET} — the tree is {repo}."
    )


def test_one_uncommitted_file_is_still_counted_in_the_warning(repo):
    """The counts are the operator's only way to tell a one-file skip from a
    fifteen-file one (both measured in the live 39)."""
    _ahead(repo, 1)
    _dirty(repo, 1)

    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.dirty_files == 1
    assert status.behind == 1
    assert str(repo) in status.warning
    assert TARGET in status.warning


def test_a_clean_tree_that_is_still_behind_is_not_reported_as_fine(repo):
    """AC10: "the warning is true whatever left the tree behind".

    A clean tree that is still behind its target means the refresh did not do
    what it was supposed to. Reporting `warning == ""` there would tell the
    operator the one thing this ticket exists to stop them believing.
    """
    _ahead(repo, 4)

    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.behind == 4
    assert status.dirty_files == 0
    assert status.warning != "", "a tree behind its target is never silently fine"
    assert str(repo) in status.warning
    assert TARGET in status.warning


def test_a_git_failure_is_not_reported_as_a_current_tree(repo, caplog):
    """AC10's one hard rule. "It is current" and "we could not tell" must not
    collapse into one answer — the conflict check that returned
    `has_conflicts: False` because the check itself threw, and reported "Ready
    to merge", is the failure this repository's no-silent-failures rule is
    named after.
    """
    with caplog.at_level("WARNING"):
        status = describe(
            repo, target_branch="integration/never-existed", ticket_external_id=TICKET
        )

    assert status.warning != "", "a check that could not run must not read as a clean bill"
    assert "could not be checked" in status.warning, status.warning
    records = [r.getMessage() for r in caplog.records]
    assert any(TICKET in message for message in records), (
        f"the failure has to name the ticket to be actionable: {records}"
    )


def test_the_counts_describe_the_tree_it_was_given_not_an_ambient_git_dir(
    repo, tmp_path, monkeypatch
):
    """`GIT_DIR` overrides `cwd`, which is why every git call in the server goes
    through `run_git`. A bare `subprocess.run(["git", ...])` here would describe
    whatever repository the server's parent process was bound to — and under a
    git hook, that is a different repository entirely.
    """
    _ahead(repo, 2)
    elsewhere = make_repo(tmp_path, name="elsewhere")
    git(elsewhere, "branch", TARGET)
    monkeypatch.setenv("GIT_DIR", str(Path(elsewhere) / ".git"))

    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.behind == 2, "the ambient GIT_DIR was allowed to redirect the read"


# ---------------------------------------------------------------------------
# lg-milestone-that-917, test-break: what a plausible-but-wrong read of the
# tree still answers correctly.
#
# `behind` and `dirty_files` are two counts, and there are three near-miss
# spellings of each that pass every test above: a symmetric `HEAD...target`,
# an untracked-only dirty count, and a describe that walks the tree with a
# command that writes to it. Each of the three reports a tree the operator can
# trust when it cannot be.
# ---------------------------------------------------------------------------


def test_the_tree_s_own_unlanded_commits_are_not_counted_as_commits_it_is_missing(repo):
    """`behind` is one-directional, like the refresh it reports on.

    Every test above puts commits on the target only, so a symmetric
    `rev-list --count HEAD...TARGET` — one character from the right spelling —
    passes all of them. Here the only extra commits are the ticket's *own*,
    which is the normal state of a worktree mid-stage: the ticket's work reaches
    its target at landing (768), never at handoff, so a tree ahead of its
    target is current, not degraded. Counting them would warn every operator on
    every handoff, which is the same as warning none of them.
    """
    commit_on(repo, "main", "own-stage-one.txt", "the ticket's own earlier stage\n")
    commit_on(repo, "main", "own-stage-two.txt", "and its second\n")

    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.behind == 0, "the ticket's own commits were counted as a stale base"
    assert status.dirty_files == 0
    assert status.warning == ""


def test_the_dirty_count_sees_every_change_the_refresh_itself_refuses_to_merge_over(repo):
    """`dirty_files` has to agree with `_refresh_onto`'s own skip condition,
    which is `git status --porcelain` being non-empty — modified, staged and
    deleted tracked files included, not only untracked ones.

    Every dirty fixture above uses untracked files, so an implementation
    counting `ls-files --others` (or `diff --name-only`, which is the mirror
    image) reports `dirty_files == 0` for a tree the refresh skipped. The
    operator is then told a stale tree is simply behind, with no mention of the
    uncommitted work that is the reason it is behind and the one thing they can
    act on.
    """
    _ahead(repo, 1)
    (repo / "seed.txt").write_text("an operator edited this\n")  # modified, unstaged
    (repo / "staged.txt").write_text("and staged this\n")
    git(repo, "add", "staged.txt")  # added, staged
    tracked = next(
        line for line in git(repo, "ls-files").stdout.splitlines() if line.endswith(".md")
    )
    (repo / tracked).unlink()  # deleted, unstaged
    porcelain = git(repo, "status", "--porcelain").stdout.splitlines()
    assert len(porcelain) == 3, f"fixture: three changes, none of them untracked: {porcelain}"

    status = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.dirty_files == 3, (
        "a dirty count that only sees untracked files reports a skipped refresh as clean"
    )
    assert status.behind == 1
    assert "3 uncommitted changes" in status.warning, status.warning


def test_a_tree_that_is_no_longer_a_repository_is_not_reported_as_current(repo, caplog):
    """AC10's rule again, on the other way the read fails: the directory the
    command would run in is gone, or is not a checkout. An operator who removed
    a worktree by hand, or a `git worktree prune` between the refresh and the
    read, both land here — and `behind == 0` would hand them a path that cannot
    be pasted at all with nothing said about it.
    """
    not_a_repo = repo.parent / "pruned"
    not_a_repo.mkdir()

    with caplog.at_level("WARNING"):
        status = describe(not_a_repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert status.warning != "", "a read that could not run must not read as a clean bill"
    assert "could not be checked" in status.warning, status.warning
    assert any(TICKET in record.getMessage() for record in caplog.records)


def test_describing_a_tree_does_not_touch_it_and_says_the_same_thing_twice(repo):
    """The two surfaces read this *after* the refresh, at the boundary, on a
    tree an operator or a harness is about to be handed. So it is a read: no
    fetch, no merge, no ref written, no index left staged — and the same tree
    describes the same way twice, which is what makes a report an operator can
    act on rather than a dice roll.
    """
    _ahead(repo, 2)
    _dirty(repo, 1)
    before_head = git(repo, "rev-parse", "HEAD").stdout.strip()
    before_refs = git(repo, "show-ref").stdout
    before_status = git(repo, "status", "--porcelain").stdout

    first = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)
    second = describe(repo, target_branch=TARGET, ticket_external_id=TICKET)

    assert first.model_dump() == second.model_dump()
    assert git(repo, "rev-parse", "HEAD").stdout.strip() == before_head
    assert git(repo, "show-ref").stdout == before_refs, "the read wrote a ref"
    assert git(repo, "status", "--porcelain").stdout == before_status, (
        "the read left changes staged or working-tree state moved"
    )


def test_prepared_tree_is_one_frozen_value_holding_exactly_the_pair(tmp_path):
    """AC1. Two fields, frozen, no defaults.

    Frozen because the two values are two readings of ONE resolution: a caller
    that can reassign `target_branch` on a tree somebody else prepared has
    re-created the mixing this ticket removes. No defaults, because a
    half-answer — a tree with no branch, or a branch with no tree — is the
    shape `_handoff_target_branch` degraded to and the shape
    `describe_handoff_tree` then rendered as "could not be checked".
    """
    from loregarden.services.handoff_tree_status import PreparedTree
    from pydantic import ValidationError

    tree = PreparedTree(repo_root=tmp_path, target_branch=TARGET)

    assert set(PreparedTree.model_fields) == {"repo_root", "target_branch"}, (
        f"PreparedTree carries {sorted(PreparedTree.model_fields)}: it is the pair and "
        "nothing else, so a third field is a second question wearing this one's name"
    )
    assert tree.repo_root == tmp_path
    assert tree.target_branch == TARGET
    for field, value in (("repo_root", tmp_path / "elsewhere"), ("target_branch", "main")):
        with pytest.raises(ValidationError):
            setattr(tree, field, value)


def test_describe_handoff_tree_takes_the_pair_as_one_parameter():
    """AC6. No signature in the codebase may take `repo_root` and
    `target_branch` as two independent arguments that must agree.

    This is the signature the defect was visible in: the two values arrived
    separately, so `api/ticket_handoff` passed a tree from
    `prepare_terminal_handoff` beside a branch it re-resolved itself, and
    `external_harness` did the same with an unguarded call.
    """
    import inspect

    from loregarden.services.handoff_tree_status import describe_handoff_tree

    params = inspect.signature(describe_handoff_tree).parameters

    assert list(params) == ["tree", "ticket_external_id"], (
        f"describe_handoff_tree takes {list(params)}: the pair is one parameter"
    )
    assert params["ticket_external_id"].kind is inspect.Parameter.KEYWORD_ONLY


def test_prepared_tree_refuses_a_half_answer(tmp_path):
    """AC1, the half the frozen check does not reach: neither field has a default.

    `test_prepared_tree_is_one_frozen_value_holding_exactly_the_pair` constructs
    one with both fields and reads `model_fields`, so
    `target_branch: str = ""` passes it — `model_fields` still has exactly the
    two keys, and the instance it builds still freezes.

    A default is the defect wearing the new type's name. `""` is precisely what
    `_handoff_target_branch` degraded to when it could not name a branch, and
    `describe_handoff_tree` renders that as "the tree could not be checked" —
    so a caller that forgot to pass the branch would get the deleted function's
    behaviour back from the value introduced to remove it, silently. There is
    no such thing as a tree without the branch it was brought up to: that
    pairing is the whole reason this is one value.
    """
    from loregarden.services.handoff_tree_status import PreparedTree
    from pydantic import ValidationError

    for kwargs in ({}, {"repo_root": tmp_path}, {"target_branch": TARGET}):
        with pytest.raises(ValidationError):
            PreparedTree(**kwargs)
