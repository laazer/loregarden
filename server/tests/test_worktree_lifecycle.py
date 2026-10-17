"""Worktrees are retired when the ticket ends, and after a crash.

The failure this closes is "server restart kills mid-run orchestration": the
row said active, the process that owned it was gone, and nothing ever looked
again. The counterpart rule is that a crash must not cost work — a tree with
uncommitted changes is kept, and a tree whose ticket is still unfinished is
left for the resume path.
"""

import dataclasses
import logging
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import worktree_lifecycle
from loregarden.services.git_subprocess import run_git
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import (
    KeepReason,
    Retirement,
    reconcile_worktrees,
    release_ticket_worktree,
    retire_worktree,
)
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import git, make_repo


def _commit_in(path: Path, name: str = "work.txt") -> None:
    """Put a commit on the worktree's branch — the thing that outlives removal."""
    (path / name).write_text("real work\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", "work")


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="proj", name="proj", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session, workspace, external_id="LG-1"):
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/{external_id.lower()}",
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _tree_for(session, workspace, ticket):
    run = AgentRun(
        run_code=f"run-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return resolve_execution_root(session, run, ticket, workspace)


def _row(session, ticket):
    return session.exec(
        Worktree.__table__.select().where(Worktree.__table__.c.ticket_id == ticket.id)
    ).first()


def test_finishing_a_ticket_removes_its_worktree(session, workspace):
    """The intended path: the work is committed to the branch, so only the
    checkout is thrown away and the commits stay in the object store."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is True
    assert not path.exists()
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


def test_an_unfinished_ticket_keeps_its_worktree(session, workspace):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)

    assert release_ticket_worktree(session, ticket) is False
    assert path.is_dir()


def test_uncommitted_work_is_never_deleted(session, workspace):
    """Marking a ticket done by hand while an agent's edits sit unstaged must
    not throw those edits away."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    (path / "unsaved.txt").write_text("not committed\n")
    ticket.state = TicketState.WONT_DO
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "unsaved.txt").exists()


def test_startup_settles_a_row_whose_directory_is_gone(session, workspace):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=session.get(Workspace, workspace.id).repo_path,
        check=True,
        capture_output=True,
    )

    assert reconcile_worktrees(session) == 1
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


def test_startup_removes_the_tree_of_a_ticket_that_finished(session, workspace):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert reconcile_worktrees(session) == 1
    assert not path.exists()


def test_a_tracked_modification_is_never_deleted(session, workspace):
    """The untracked case has a sibling: an agent editing a file that already
    existed leaves nothing new on disk to notice, only a changed one."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    (path / "seed.txt").write_text("edited, never committed\n")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "seed.txt").read_text() == "edited, never committed\n"


def test_untracked_work_survives_a_repo_that_hides_untracked_files(session, workspace):
    """`status.showUntrackedFiles=no` turns a bare `--porcelain` into a rubber
    stamp: git reports a clean tree while the agent's new files sit in it."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    git(path, "config", "status.showUntrackedFiles", "no")
    (path / "brand-new.py").write_text("never committed\n")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "brand-new.py").exists()


def test_a_branch_with_nothing_committed_is_never_retired(session, workspace):
    """The external-harness shape: the tree is clean because nothing was ever
    written back, and the branch carries no commits, so the object store would
    hold nothing after the checkout went. Refuse rather than assume."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert path.is_dir()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_startup_keeps_a_finished_tickets_tree_when_nothing_was_committed(session, workspace):
    """Reconciliation retires on the same signal and needs the same proof."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert reconcile_worktrees(session) == 0
    assert path.is_dir()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_startup_reports_every_kept_tree_in_one_grouped_record(session, workspace, caplog):
    """A restart keeping many trees says so once, grouped by why, not once per tree."""
    dirty_ticket = _ticket(session, workspace, external_id="LG-1")
    dirty = _tree_for(session, workspace, dirty_ticket)
    (dirty / "unsaved.txt").write_text("not committed\n")
    empty_ticket = _ticket(session, workspace, external_id="LG-2")
    empty = _tree_for(session, workspace, empty_ticket)
    for ticket in (dirty_ticket, empty_ticket):
        ticket.state = TicketState.DONE
        session.add(ticket)
    session.commit()

    with caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"):
        assert reconcile_worktrees(session) == 0

    assert len(caplog.records) == 1
    summary = caplog.records[0].getMessage().splitlines()
    uncommitted = summary.index(f"  {KeepReason.UNCOMMITTED.value} (1):")
    nothing = summary.index(f"  {KeepReason.NOTHING_COMMITTED.value} (1):")
    assert str(dirty) in summary[uncommitted + 1]
    assert str(empty) in summary[nothing + 1]


def test_startup_leaves_a_live_ticket_alone_so_the_resume_finds_its_work(session, workspace):
    """The crash-and-restart case: the run died, the tree survived, and the
    resume path needs it exactly as it was."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    (path / "half-done.txt").write_text("survived the crash\n")

    assert reconcile_worktrees(session) == 0
    assert (Path(path) / "half-done.txt").exists()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_a_commit_on_a_detached_head_counts_for_nothing(session, workspace):
    """The commit count is asked of the branch, not of the tree's HEAD: a commit
    made while HEAD is detached has no branch ref keeping it reachable, so
    removing the checkout would strand it exactly as deleting uncommitted work
    would."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    git(path, "checkout", "-q", "--detach")
    _commit_in(path, "detached.txt")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "detached.txt").exists()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_a_branch_whose_base_cannot_be_resolved_is_never_retired(session, workspace):
    """An unprovable premise is not a satisfied one. The branch this worktree was
    cut from is gone, so nothing can prove the branch's commits would outlive the
    checkout — and a tree that cannot be proved safe to delete is kept."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    row.parent_branch = "release/deleted-after-the-merge"
    session.add(row)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert path.is_dir()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_a_checkout_git_cannot_read_is_never_retired(session, workspace):
    """The dirty check fails closed: a `git status` that exits non-zero means we
    cannot tell what the tree holds, which is not a licence to delete it. A
    corrupt index is the shape a crash mid-write leaves, and it is the case where
    everything else about the tree still looks retirable."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    admin = Path(git(path, "rev-parse", "--absolute-git-dir").stdout.strip())
    (admin / "index").write_bytes(b"not an index")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "work.txt").exists()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_a_tree_that_is_both_dirty_and_empty_names_the_condition_an_operator_can_act_on(
    session, workspace, caplog
):
    """Both premises fail at once — the external-harness shape with edits still
    in it. The operator needs the reason that tells them what to do, and
    "uncommitted changes" is that one: it says the tree holds work to commit,
    where "no commits beyond its base" would send them to look at a branch whose
    emptiness is a consequence rather than the cause."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    (path / "unsaved.txt").write_text("not committed\n")

    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    with caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"):
        assert release_ticket_worktree(session, ticket) is False

    assert (path / "unsaved.txt").exists()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value
    assert KeepReason.UNCOMMITTED.value in caplog.text
    assert KeepReason.NOTHING_COMMITTED.value not in caplog.text


def test_startup_judges_every_tree_independently_in_one_pass(session, workspace):
    """A kept tree must not stop the pass, and a retired one must not sweep its
    neighbours along. Four trees in the four states reconciliation can meet, in
    an order that puts a refusal first: a single `break` where the loop means
    `continue`, or a reason read from the previous row, shows up here and nowhere
    in the single-tree tests."""
    dirty_ticket = _ticket(session, workspace, external_id="LG-DIRTY")
    dirty = _tree_for(session, workspace, dirty_ticket)
    (dirty / "unsaved.txt").write_text("not committed\n")

    committed_ticket = _ticket(session, workspace, external_id="LG-COMMITTED")
    committed = _tree_for(session, workspace, committed_ticket)
    _commit_in(committed)

    empty_ticket = _ticket(session, workspace, external_id="LG-EMPTY")
    empty = _tree_for(session, workspace, empty_ticket)

    gone_ticket = _ticket(session, workspace, external_id="LG-GONE")
    gone = _tree_for(session, workspace, gone_ticket)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(gone)],
        cwd=session.get(Workspace, workspace.id).repo_path,
        check=True,
        capture_output=True,
    )

    for ticket in (dirty_ticket, committed_ticket, empty_ticket, gone_ticket):
        ticket.state = TicketState.DONE
        session.add(ticket)
    session.commit()

    # The committed tree and the vanished row, and only those two.
    assert reconcile_worktrees(session) == 2

    assert (dirty / "unsaved.txt").exists()
    assert _row(session, dirty_ticket).state == WorktreeState.ACTIVE.value
    assert empty.is_dir()
    assert _row(session, empty_ticket).state == WorktreeState.ACTIVE.value
    assert not committed.exists()
    assert _row(session, committed_ticket).state == WorktreeState.CLEANUP.value
    assert _row(session, gone_ticket).state == WorktreeState.CLEANUP.value


def test_a_refusal_repeated_leaves_the_row_exactly_as_it_found_it(session, workspace):
    """`_settle_manual_state_change` calls this on every state write, so a
    finished ticket somebody touches twice reaches it twice. A refusal that
    moved the row out of ACTIVE on the way out would hand the second call a tree
    reconciliation no longer looks at, and the work would stop being reported as
    kept while still sitting on disk."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    (path / "unsaved.txt").write_text("not committed\n")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    for _ in range(3):
        assert release_ticket_worktree(session, ticket) is False
        assert (path / "unsaved.txt").read_text() == "not committed\n"
        assert _row(session, ticket).state == WorktreeState.ACTIVE.value

    # And reconciliation still sees it, which is what keeps it reported.
    assert reconcile_worktrees(session) == 0
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_a_row_whose_directory_already_went_is_still_settled(session, workspace):
    """The guard is about work on disk, so where there is no disk there is
    nothing to guard. A precondition that refused this case instead would pin
    the row at ACTIVE forever — the leaked-row failure this module was written
    to close — and reconciliation would report it as kept work every boot."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=session.get(Workspace, workspace.id).repo_path,
        check=True,
        capture_output=True,
    )
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is True
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


# --------------------------------------------------------------------------
# HEAD against every ref — "does anything die when we remove this checkout?"
#
# The premises above ask whether anything *survives* removing a checkout, and
# that question is correctly asked of the branch. These ask the other one, of
# HEAD: an interrupted rebase, a bisect, or a bare `checkout --detach` followed
# by a commit leaves a commit that no branch, tag or remote ref can reach, and
# both of the branch-shaped premises hold while it sits there.
# --------------------------------------------------------------------------


def _detach_and_commit(path: Path, name: str) -> str:
    """Move HEAD off the branch and commit there. Returns the stranded commit."""
    git(path, "checkout", "-q", "--detach")
    (path / name).write_text("committed off the branch\n")
    git(path, "add", "-A")
    git(path, "commit", "-q", "-m", f"{name}, nowhere to land")
    return git(path, "rev-parse", "HEAD").stdout.strip()


def test_a_commit_only_the_checkout_can_reach_is_never_deleted(session, workspace):
    """The shape an interrupted `git rebase` or a `git bisect` leaves behind.

    Both branch-shaped premises are genuinely satisfied — the tree is clean and
    the branch holds a commit its base does not — so the checkout looks finished
    from outside. What neither premise sees is that HEAD has moved off the branch
    and taken a commit with it, which removing the checkout would strand exactly
    as deleting uncommitted work would.

    Moved here from `test_open_defect_repros.py`, where it was the open repro for
    this ticket; the fix makes it a permanent regression test for this surface.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    stranded = _detach_and_commit(path, "more.txt")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert path.is_dir()
    assert (path / "more.txt").exists()
    assert git(path, "rev-parse", "HEAD").stdout.strip() == stranded
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_a_stranded_head_is_reported_ahead_of_an_empty_branch(session, workspace, caplog):
    """The one shape both the new premise and the empty-branch premise claim, so
    it is the only place the cascade's order is observable: HEAD carries a commit
    *and* the branch carries none beyond its base.

    The reason an operator is given has to be the one they can act on. "a commit
    no branch or tag reaches" tells them to put a branch on it; "no commits
    beyond its base" is true, and sends them to inspect a branch whose emptiness
    is a consequence rather than the cause — the same call this module already
    made for a tree that is both dirty and empty.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _detach_and_commit(path, "stranded.txt")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    with caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"):
        assert reconcile_worktrees(session) == 0

    assert path.is_dir()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value
    summary = caplog.records[0].getMessage().splitlines()
    stranded = summary.index(f"  {KeepReason.STRANDED_HEAD.value} (1):")
    assert str(path) in summary[stranded + 1]
    assert f"  {KeepReason.NOTHING_COMMITTED.value} (1):" not in summary


def test_a_checkout_detached_at_a_branch_tip_is_still_retired(session, workspace):
    """The `git bisect` / `git checkout --detach` shape, with no commit of its own.

    This is what separates asking HEAD *against every ref* from asking the much
    easier question "is HEAD detached". Nothing here is unreachable: HEAD is the
    branch tip. Refusing this shape would keep every bisected checkout forever,
    which is the leak this module exists to stop.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    git(path, "checkout", "-q", "--detach")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is True
    assert not path.exists()
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


def test_a_detached_commit_a_tag_can_reach_is_retired(session, workspace):
    """A tag is a ref, and a ref is what keeps a commit reachable once the
    checkout is gone. The question is reachability, not attachment, so a tagged
    detached commit is retired and the commit survives in the object store —
    which is the whole argument for removing any checkout."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    stranded = _detach_and_commit(path, "more.txt")
    git(path, "tag", "keep-the-rebase")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is True
    assert not path.exists()
    repo_path = session.get(Workspace, workspace.id).repo_path
    assert git(repo_path, "cat-file", "-t", stranded).stdout.strip() == "commit"


def test_a_checkout_whose_refs_git_cannot_list_is_never_retired(session, workspace):
    """The new premise fails closed, like the two beside it.

    A ref pointing at an object the repository does not have is the shape a
    crash mid-write leaves, and it is the case where everything else still looks
    retirable: `git status` reports a clean tree and the branch's own commit
    count is readable, while enumerating refs exits non-zero. An unprovable
    premise is not a satisfied one, so the checkout stays.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    common = git(path, "rev-parse", "--path-format=absolute", "--git-common-dir")
    admin = Path(common.stdout.strip())
    (admin / "refs" / "heads" / "broken").write_text("deadbeef" * 5 + "\n")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is False
    assert (path / "work.txt").exists()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


# --------------------------------------------------------------------------
# Adversarial cover for the third premise: the question it asks git, the
# answers it refuses to trust, where it sits in the cascade, and the shapes it
# must *not* claim.
# --------------------------------------------------------------------------


def _argv(call: mock._Call) -> list[str]:
    """The git arguments of a recorded `run_git` call, however it was passed."""
    args = call.args[0] if call.args else call.kwargs["args"]
    return list(args)


#: The one question AC2 pins. Any other spelling answers 0 for the shape this
#: module is guarding against (`--not --all` includes HEAD itself) or over-
#: refuses a shape some ref still reaches (dropping `--tags`/`--remotes`).
REF_REACH_ARGV = [
    "rev-list",
    "--count",
    "HEAD",
    "--not",
    "--branches",
    "--tags",
    "--remotes",
]


def _is_ref_reach(argv: list[str]) -> bool:
    """Loose match on "HEAD against other refs", whatever form it took.

    Deliberately not `== REF_REACH_ARGV`: a stub keyed on the exact spelling
    would quietly stop firing for a wrong one, and a test whose mutation stops
    being applied passes for the wrong reason. `_has_preserved_commits`' own
    `rev-list base..branch` is excluded — it has no `--not` and no bare `HEAD`.
    """
    return argv[:1] == ["rev-list"] and "--not" in argv and "HEAD" in argv


def test_the_premise_asks_git_which_commits_only_this_checkout_reaches(session, workspace):
    """The exact question, asked of the checkout, in the one form that answers it.

    `--remotes` is otherwise inert under test (the fixture repos have no remote)
    and `--tags`'s only behavioural cover is one shape, so the form itself is
    pinned here: every near-miss spelling is wrong in a way no fixture shows.
    `HEAD --not --all` answers 0 for this very tree because `--all` includes
    HEAD; dropping `--branches` claims every ordinary checkout.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    _detach_and_commit(path, "more.txt")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    with mock.patch.object(worktree_lifecycle, "run_git", wraps=run_git) as spy:
        assert release_ticket_worktree(session, ticket) is False

    asked = [call for call in spy.call_args_list if _argv(call) == REF_REACH_ARGV]
    assert len(asked) == 1, [_argv(call) for call in spy.call_args_list]
    assert asked[0].kwargs["cwd"] == str(path)
    assert asked[0].kwargs["check"] is False


def test_a_ref_count_git_answers_with_something_other_than_a_number_keeps_the_tree(
    session, workspace, caplog
):
    """Fail closed on a zero exit whose stdout is not a count.

    The sibling premises both refuse what they cannot prove, and `--count`
    printing something unparseable is the half of that rule no repository state
    can reach: a broken ref makes `rev-list` exit non-zero, never exit 0 with
    prose. So the answer is substituted directly, on an otherwise perfectly
    retirable tree — clean, attached HEAD, a commit on the branch — leaving the
    unreadable count as the only thing standing between it and deletion. A
    truthy-stdout read, or `int(...)` wrapped in a swallowed except, keeps
    nothing and deletes this tree.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    def unreadable_count(args, **kwargs):
        if _is_ref_reach(list(args)):
            return subprocess.CompletedProcess(
                list(args), 0, "warning: refs/heads/wip does not point to a valid object\n", ""
            )
        return run_git(args, **kwargs)

    with (
        mock.patch.object(worktree_lifecycle, "run_git", side_effect=unreadable_count) as spy,
        caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"),
    ):
        assert release_ticket_worktree(session, ticket) is False

    # The substitution really was applied — otherwise this test would pass on a
    # tree nothing was ever done to.
    assert [call for call in spy.call_args_list if _is_ref_reach(_argv(call))]
    assert (path / "work.txt").exists()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value
    assert KeepReason.STRANDED_HEAD.value in caplog.text


def test_a_tree_that_is_both_dirty_and_stranded_names_the_work_that_has_no_commit(
    session, workspace, caplog
):
    """Order, at the top of the cascade this time.

    An interrupted rebase that also left edits behind satisfies two refusals at
    once. The one worth saying is the uncommitted work: that is what removing
    the checkout would destroy outright, where the stranded commit survives in
    the object store and only loses its last reference. Running the new premise
    first would report a recoverable loss and stay silent about the total one.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    _detach_and_commit(path, "more.txt")
    (path / "still_unsaved.txt").write_text("not committed anywhere\n")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    with caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"):
        assert release_ticket_worktree(session, ticket) is False

    assert (path / "still_unsaved.txt").exists()
    assert (path / "more.txt").exists()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value
    assert KeepReason.UNCOMMITTED.value in caplog.text
    assert KeepReason.STRANDED_HEAD.value not in caplog.text


def test_a_checkout_detached_at_an_ancestor_is_still_retired(session, workspace):
    """What `git bisect` actually leaves: HEAD mid-history, not at the tip.

    A bisect run checks out a commit *between* the endpoints, so the
    detached-at-the-tip case is not the shape that leaks — this one is, and it
    is the one an implementation that compares HEAD against the branch tip, or
    merely asks "is HEAD detached", refuses forever. Nothing here is
    unreachable: every commit HEAD reaches, the branch reaches too.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path, "first.txt")
    _commit_in(path, "second.txt")
    git(path, "checkout", "-q", "--detach", "HEAD~1")
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    assert release_ticket_worktree(session, ticket) is True
    assert not path.exists()
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


@pytest.mark.parametrize("reason", list(KeepReason), ids=lambda r: r.name)
def test_every_keep_reason_refuses_the_removal_and_says_so(session, workspace, reason, caplog):
    """The vocabulary, not a list of branches — including members not yet written.

    `retire_worktree`'s fallthrough is `remove_checkout`, so a `KeepReason`
    `_keep_reason` can return and `retire_worktree` has no branch for deletes
    the checkout it was meant to protect. Per-member `if`s pass every behavioural
    test in this module and still hold that hazard open for the next member;
    this parametrization grows with the enum, so the member added after this one
    cannot fall through silently. The reason is returned rather than a bare
    False, because the chat sweep has to report which premise fired, and the
    refusal is logged at `warning` — below that is reported nowhere.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)

    with (
        mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=reason),
        caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"),
    ):
        outcome = retire_worktree(session, service, row)

    # Field reads, not a restructure: the single call and the single WARNING
    # assertion below are 951's, and `.failure == ""` is what separates a
    # premise that *chose* to keep the tree from a removal that was tried and
    # failed. Collapsing those two was lg-external-harness-954.
    assert outcome.keep_reason is reason
    assert outcome.removed is False
    assert outcome.failure == ""
    assert outcome.kept_because == reason.value
    service.remove_checkout.assert_not_called()
    assert path.is_dir()
    refusals = [record for record in caplog.records if record.levelno == logging.WARNING]
    assert len(refusals) == 1
    assert str(path) in refusals[0].getMessage()


def test_a_checkout_no_premise_claims_is_handed_to_cleanup(session, workspace):
    """The other half of the inversion: refusing on `reason is not None` must not
    turn into refusing everything. With every premise satisfied the removal goes
    through, and `removed` — set by this function's own branching, not copied
    from the service's answer — is what says so."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)
    # `None` is the settled answer now. A `MagicMock`'s default return is a
    # truthy `Mock`, which under the new seam is an unknown refusal, so leaving
    # it unset would make this test assert the opposite of what it is named for.
    service.remove_checkout.return_value = None

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    assert outcome.removed is True
    assert outcome.keep_reason is None
    assert outcome.refusal is None
    service.remove_checkout.assert_called_once_with(row)
    # The stub did not actually remove it; the call is the assertion.
    assert path.is_dir()


# --------------------------------------------------------------------------
# A removal that was attempted and failed — lg-external-harness-954
#
# `remove_checkout` returns a `CleanupRefusal` member for a path outside the
# service's base directory (OUTSIDE_BASE) and for any exception out of `git
# worktree remove` plus the `shutil.rmtree` fallback (REMOVE_RAISED): a
# permission error, a file another process holds
# open. The three premises above all held, so there is no `KeepReason` to
# report, and the checkout is still on disk. Those two facts together are the
# answer every caller needs, and the enum could only carry one of them: before
# this, a failed removal and a completed one were the same value, so
# `release_ticket_worktree` said True about a directory that was still there,
# `delete_chat_session` dropped the row that owned it, and the sweep deleted the
# branch out from under it. "It failed" and "there is nothing" must not collapse
# into one answer (CLAUDE.md, *No silent failures*).
#
# The lever throughout is a `MagicMock(spec=WorktreeService)` whose
# `remove_checkout` returns a `CleanupRefusal` member with `_keep_reason`
# patched to None, because that is the one state in which the distinction
# exists at all — and the one a wrong field read passes. Never `False`:
# `CleanupRefusal | None` is falsy on success, so a stub still answering
# `False` would read as a settled row (lg-external-harness-1165 AC21).
# --------------------------------------------------------------------------

#: The clause `retire_worktree` reports a retryable failed removal with, pinned
#: here byte-for-byte because lg-external-harness-954 measured the toast that
#: carries it and lg-external-harness-1165 AC2/AC10 keep that measurement valid.
REMOVAL_FAILED = "removing its checkout failed"

#: Its never-retryable counterpart (lg-external-harness-1165 AC2). A different
#: clause for a different cause: "removing its checkout failed" invites a retry,
#: and no retry can move a checkout the service will not touch.
REMOVAL_REFUSED_OUTSIDE_BASE = "its checkout is outside the worktrees folder"

#: Which clause each member is reported with. Mirrors the mapping
#: `retire_worktree` reads, so a member added without a clause fails the
#: parametrized test below rather than silently reporting the wrong sentence.
FAILURE_CLAUSE = {
    "REMOVE_RAISED": REMOVAL_FAILED,
    "OUTSIDE_BASE": REMOVAL_REFUSED_OUTSIDE_BASE,
}


def _failing_service(refusal: CleanupRefusal = CleanupRefusal.REMOVE_RAISED) -> mock.MagicMock:
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)
    service.remove_checkout.return_value = refusal
    return service


def test_a_removal_that_was_tried_and_failed_is_not_reported_as_a_removal(session, workspace):
    """AC1/AC14 — the checkout is still there, and the answer says so.

    Every premise held, so `keep_reason` is None: nothing *chose* to keep this
    tree. `removed` is still False, because the removal did not happen, and
    `failure` is what tells the two Nones apart.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = _failing_service()

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    service.remove_checkout.assert_called_once_with(row)
    assert outcome.removed is False
    assert outcome.keep_reason is None
    assert outcome.failure == REMOVAL_FAILED
    assert outcome.kept_because == REMOVAL_FAILED
    assert path.is_dir()


def test_a_successful_removal_has_nothing_to_say_about_why_it_kept_the_tree(session, workspace):
    """AC14 — `kept_because` is words for a caller to print, and on the happy
    path there are none. A caller that printed something here would be
    announcing a refusal that never happened."""
    ticket = _ticket(session, workspace)
    _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)
    service.remove_checkout.return_value = None

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    assert outcome.removed is True
    assert outcome.failure == ""
    assert outcome.kept_because == ""


def test_the_failure_is_reported_once_from_this_module_and_names_the_tree(
    session, workspace, caplog
):
    """AC5/AC16 — observable from here, and exactly once.

    `remove_checkout`'s own `logger.error` names the cause but not the
    decision, and a caller reading only this module's records would otherwise
    see a silent success. One record, not two: the warning already exists on
    this branch, and the live mistake is adding a second one beside it or in a
    caller, which turns one failure into a pair of unrelated-looking lines.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = _failing_service()

    with (
        mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None),
        caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"),
    ):
        retire_worktree(session, service, row)

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name == "loregarden.services.worktree_lifecycle"
    ]
    assert len(warnings) == 1
    assert str(path) in warnings[0].getMessage()


def test_the_services_answer_is_never_passed_through_as_the_removal_flag(session, workspace):
    """AC14 — `removed` is this function's own decision, not the seam's answer.

    Under the bool seam the lever was a `MagicMock` whose unset return is a
    truthy `Mock`. The seam now answers `CleanupRefusal | None`, so the
    passthrough this guards against inverted with it: `removed` must be the
    answer to "is the checkout gone", derived from `refusal is None`, and never
    the seam's own value handed on. The fallthrough hazard — a member added
    later with no branch for it — is covered instead by
    `test_each_refusal_member_is_reported_with_its_own_clause`, which
    parametrizes over the live enum and so grows with it. It is not probed with
    a sentinel here: the reporting branch legitimately reads the member, so a
    sentinel would only prove that a non-member is not a member.

    What this cannot catch, verified by mutation rather than assumed:
    `removed=bool(...)` survives it, because `bool` of a truthy `Mock` *is*
    `True`. AC14 reads as though this assertion forbids the `bool(...)` call;
    it does not, and no behavioural test can — a coercion of a value that is
    already truthy is unobservable. `bool(...)` here is a code-shape rule (it is
    defensive normalization of a value whose type is already known), so it
    belongs to the organization gate and the reviewer, not to this test.
    """
    ticket = _ticket(session, workspace)
    _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)
    service.remove_checkout.return_value = None

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        settled = retire_worktree(session, service, row)

    assert settled.removed is True
    assert settled.failure == ""
    assert settled.refusal is None

    # And the other direction, with a real member: `removed` is False, and it
    # is this function's own decision rather than the member handed on.
    service.remove_checkout.return_value = CleanupRefusal.REMOVE_RAISED
    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        refused = retire_worktree(session, service, row)

    assert refused.removed is False
    assert refused.refusal is CleanupRefusal.REMOVE_RAISED


@pytest.mark.parametrize("refusal", list(CleanupRefusal), ids=lambda r: r.name)
def test_each_refusal_member_is_reported_with_its_own_clause(session, workspace, refusal):
    """AC1/AC2 — the two causes do not share a sentence, and the member travels.

    The vocabulary, not a list of branches: parametrizing over `CleanupRefusal`
    means the member added after these two cannot quietly inherit whichever
    clause the mapping happens to default to. `failure` carries words for a
    caller to print and `refusal` carries the member itself, because AC3's boot
    log and AC9's toast need *different* sentences for the same cause — so the
    prose cannot be the discriminator (lg-external-harness-1165 AC16).
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = _failing_service(refusal)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    service.remove_checkout.assert_called_once_with(row)
    assert outcome.removed is False
    assert outcome.keep_reason is None
    assert outcome.refusal is refusal
    assert outcome.failure == FAILURE_CLAUSE[refusal.name]
    assert outcome.kept_because == FAILURE_CLAUSE[refusal.name]
    assert path.is_dir()


def test_the_two_failure_clauses_are_distinct_module_constants():
    """AC2/AC10 — two clauses, two constants, and `REMOVAL_FAILED` unmoved.

    `REMOVAL_FAILED`'s value is byte-identical to what lg-external-harness-954
    measured against the 4-line toast clamp, so the retryable path keeps that
    evidence. The never-retryable clause is a second constant rather than a
    reworded first one, because collapsing them is the defect restated.
    """
    assert worktree_lifecycle.REMOVAL_FAILED == "removing its checkout failed"
    assert (
        worktree_lifecycle.REMOVAL_REFUSED_OUTSIDE_BASE
        == "its checkout is outside the worktrees folder"
    )
    assert len(set(FAILURE_CLAUSE.values())) == len(FAILURE_CLAUSE)
    assert set(FAILURE_CLAUSE) == {member.name for member in CleanupRefusal}


def test_the_retirement_gained_one_defaulted_field_and_kept_the_other_three():
    """AC2 — `refusal` is additive and defaulted, so nothing existing re-writes.

    The four existing names, types and defaults are what every current caller
    and every current test of `Retirement` reads; the new field exists only so
    `delete_chat_session` can pick a sentence without parsing operator prose. A
    `Retirement` built the way it was built before this ticket must still be
    constructible, and must answer `None` for the field it never heard of.
    """
    fields = {f.name: f for f in dataclasses.fields(Retirement)}
    assert list(fields) == ["removed", "keep_reason", "failure", "refusal"]
    assert fields["keep_reason"].default is None
    assert fields["failure"].default == ""
    assert fields["refusal"].default is None
    assert Retirement(removed=True).refusal is None
    assert Retirement(removed=False, failure=REMOVAL_FAILED).refusal is None
    # No validator: `test_a_failure_outranks_a_premise_...` below legitimately
    # builds a `failure` with no member, and a `__post_init__` pairing the two
    # would reject it (AC2).
    assert "__post_init__" not in vars(Retirement)


def test_the_failure_clause_is_one_module_constant(session, workspace):
    """AC14 — the clause is defined once, where both branches and any future
    caller read the same string. Spelled inline at each use it drifts, and a
    test pinning one spelling then passes while the other has changed."""
    constants = [
        name
        for name in dir(worktree_lifecycle)
        if not name.startswith("__")
        and getattr(worktree_lifecycle, name, None) == REMOVAL_FAILED
        and isinstance(getattr(worktree_lifecycle, name), str)
    ]
    assert len(constants) == 1, f"expected one module constant holding the clause, got {constants}"


def test_a_retirement_cannot_be_rewritten_after_the_fact(session, workspace):
    """AC14 — frozen, because this travels to a caller that decides whether to
    delete a branch. A mutable answer is one an intermediate layer can
    "normalize" into the success it was supposed to prevent."""
    outcome = Retirement(removed=False, failure=REMOVAL_FAILED)

    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.removed = True  # type: ignore[misc]


def test_the_keep_reason_vocabulary_is_still_exactly_three_members():
    """AC14 — the failure is carried beside the enum, not inside it. A fourth
    member would make every `for reason in KeepReason` summary and every
    `_reason_detail` branch answer for a case no premise can return."""
    assert [reason.name for reason in KeepReason] == [
        "UNCOMMITTED",
        "STRANDED_HEAD",
        "NOTHING_COMMITTED",
    ]


def test_a_finished_ticket_whose_checkout_will_not_go_is_not_reported_released(
    session, workspace, caplog
):
    """AC2 — the caller's bool follows `removed`, not "no premise refused".

    Both callers of this discard the answer today, so nothing yet notices. It is
    pinned because the answer is what the function promises in its name, and the
    next caller to read it will read it as "the checkout is gone": a caller told
    True stops looking at a checkout nothing will ever reap, so the directory
    still being there and the row still owning it are asserted beside the bool.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    with (
        mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None),
        mock.patch.object(
            WorktreeService, "remove_checkout", return_value=CleanupRefusal.REMOVE_RAISED
        ),
        caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"),
    ):
        assert release_ticket_worktree(session, ticket) is False

    assert path.is_dir()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value
    assert str(path) in caplog.text


def test_startup_does_not_count_a_vanished_rows_failed_cleanup_as_settled(
    session, workspace, caplog
):
    """AC19 — reconcile's first direct `remove_checkout` call stops being discarded.

    The directory is gone, so there is no work to lose and the row is stale
    bookkeeping — but if stamping it CLEANUP fails, the row is still ACTIVE and
    the count must not claim otherwise. Counting it anyway reports "reconciled
    1" every boot for a row that never moves, which is the leaked-row failure
    this module exists to close, reported as its own fix.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=session.get(Workspace, workspace.id).repo_path,
        check=True,
        capture_output=True,
    )
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()

    with (
        mock.patch.object(
            WorktreeService, "remove_checkout", return_value=CleanupRefusal.REMOVE_RAISED
        ),
        caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"),
    ):
        assert reconcile_worktrees(session) == 0

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name == "loregarden.services.worktree_lifecycle"
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert row.id in message
    assert str(path) in message


def test_startup_says_so_when_a_finished_trees_removal_fails(session, workspace, caplog):
    """AC19 — reconcile's second direct `remove_checkout` call gains its `else`.

    `elif service.remove_checkout(worktree) is None: settled += 1` already declines to
    count this, so the count was never wrong here — but a False fell out of the
    `elif` with no log and no `kept` entry, which is the one shape *No silent
    failures* names outright: a tree still on disk, reported nowhere, three
    lines from the branch that does report one.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()

    with (
        mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None),
        mock.patch.object(
            WorktreeService, "remove_checkout", return_value=CleanupRefusal.REMOVE_RAISED
        ),
        caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"),
    ):
        assert reconcile_worktrees(session) == 0

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name == "loregarden.services.worktree_lifecycle"
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert row.id in message
    assert str(path) in message
    assert path.is_dir()


# --------------------------------------------------------------------------
# The boot log reads differently per cause — lg-external-harness-1165 AC3/AC13
#
# `reconcile_worktrees` re-examines every ACTIVE row on every boot. A row whose
# recorded path is outside its service's base is refused before anything is
# attempted, so it is refused again on the next boot, and the next: a warning
# identical to the one a busy file produced once says "transient" about a fault
# only an operator editing the row or the workspace's `repo_path` can clear.
#
# AC20 — every count below is scoped to the `loregarden.services.
# worktree_lifecycle` logger. The `loregarden.services.worktree_service`
# traceback that AC18 keeps on the REMOVE_RAISED path is a different logger and
# a different record, and does not violate "one record per row per pass". Do
# not relax these assertions by widening the filter.
# --------------------------------------------------------------------------


def _out_of_base_row(session, workspace, ticket, tmp_path) -> Worktree:
    """An ACTIVE row whose recorded path is outside the base and not on disk.

    The deterministic OUTSIDE_BASE recipe, and it needs no relocation of a real
    directory and no `chmod`: `_keep_reason` returns None for a path that is
    absent from disk (worktree_lifecycle.py), so the row reaches the seam with
    no premise refusal, and `remove_checkout` refuses it on the base check
    before it ever looks at the filesystem.
    """
    run = AgentRun(
        run_code=f"run-outside-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        worktree_path=str(tmp_path / "somewhere-else" / "tree"),
        branch=ticket.branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _lifecycle_records(caplog) -> list[logging.LogRecord]:
    # AC20 — this module's logger only. Widening this filter would let the
    # service's REMOVE_RAISED traceback count as a second report.
    return [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name == "loregarden.services.worktree_lifecycle"
    ]


def test_startup_reports_a_permanent_refusal_at_error_naming_the_base_it_is_outside(
    session, workspace, tmp_path, caplog
):
    """AC3/AC13/AC17 — the never-retryable report, with no stub at the seam.

    The real `WorktreeService` answers OUTSIDE_BASE here: nothing was
    attempted, so there is no transient condition to wait out. The record names
    the row, the recorded path and the base it falls outside of, which is what
    a reader needs to fix the row or the workspace's `repo_path` — a boot count
    tells them nothing, because this one repeats forever.
    """
    ticket = _ticket(session, workspace)
    row = _out_of_base_row(session, workspace, ticket, tmp_path)
    service = worktree_lifecycle.WorktreeService(
        session, repo_path=session.get(Workspace, workspace.id).repo_path
    )

    with caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"):
        assert reconcile_worktrees(session) == 0

    records = _lifecycle_records(caplog)
    assert len(records) == 1, [r.getMessage() for r in records]
    assert records[0].levelno == logging.ERROR
    message = records[0].getMessage()
    assert row.id in message
    assert row.worktree_path in message
    assert str(service.worktree_base) in message
    # AC13 — it must not borrow the retryable sentence, which invites the retry
    # that can never work.
    assert "could not be cleaned up at startup" not in message
    # AC17 — the row is not counted settled and is left ACTIVE for the operator.
    session.refresh(row)
    assert row.state == WorktreeState.ACTIVE


def test_startup_tells_the_two_causes_apart_in_one_pass(session, workspace, tmp_path, caplog):
    """AC3/AC13/AC20 — different records, different levels, one per row.

    Two rows, one pass, one cause each. Separate tests cannot show that a log
    reader can tell them apart, because each would pass against an
    implementation that writes the same sentence at the same level for both.
    The retryable row keeps today's warning verbatim; the permanent one is its
    own record at `error`.
    """
    stuck = _ticket(session, workspace, external_id="LG-STUCK")
    stuck_path = _tree_for(session, workspace, stuck)
    _commit_in(stuck_path)
    stuck.state = TicketState.DONE
    session.add(stuck)
    session.commit()
    stuck_row = session.exec(select(Worktree).where(Worktree.ticket_id == stuck.id)).one()

    outside = _ticket(session, workspace, external_id="LG-OUTSIDE")
    outside_row = _out_of_base_row(session, workspace, outside, tmp_path)

    real_remove = WorktreeService.remove_checkout

    def raise_only_for_the_stuck_one(self, worktree):
        if worktree.id == stuck_row.id:
            return CleanupRefusal.REMOVE_RAISED
        return real_remove(self, worktree)

    with (
        mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None),
        mock.patch.object(
            WorktreeService,
            "remove_checkout",
            autospec=True,
            side_effect=raise_only_for_the_stuck_one,
        ),
        caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"),
    ):
        assert reconcile_worktrees(session) == 0

    records = _lifecycle_records(caplog)
    # Exactly one record per row per pass (AC13), this module's logger only
    # (AC20) — not one per row per cause, and not a blanket line for the pass.
    assert len(records) == 2, [r.getMessage() for r in records]
    by_row = {}
    for record in records:
        message = record.getMessage()
        if stuck_row.id in message:
            by_row["retryable"] = record
        elif outside_row.id in message:
            by_row["permanent"] = record
    assert set(by_row) == {"retryable", "permanent"}, [r.getMessage() for r in records]

    retryable, permanent = by_row["retryable"], by_row["permanent"]
    # Two levels, so a reader scanning levels gets the distinction without
    # reading the sentence...
    assert retryable.levelno == logging.WARNING
    assert permanent.levelno == logging.ERROR
    # ...and two sentences, so one reading the text gets it too.
    assert retryable.getMessage() != permanent.getMessage()
    # Each names its own path and nothing of the other row's (AC13).
    assert str(stuck_path) in retryable.getMessage()
    assert outside_row.worktree_path in permanent.getMessage()
    assert outside_row.worktree_path not in retryable.getMessage()
    assert str(stuck_path) not in permanent.getMessage()
    # The retryable sentence is today's, unchanged (AC13).
    assert "could not be cleaned up at startup" in retryable.getMessage()


def test_startup_still_settles_a_row_it_can_settle_beside_one_it_never_will(
    session, workspace, tmp_path, caplog
):
    """AC17 — `settled` counts `None` and nothing else, per row.

    The permanent refusal above repeats every boot. The row beside it must
    still go, and must still be counted: a pass that returned early, or that
    counted the refusal, would make the boot count stop meaning "rows that
    moved" — which is the leaked-row failure reported as its own fix.
    """
    gone = _ticket(session, workspace, external_id="LG-GONE")
    gone_path = _tree_for(session, workspace, gone)
    _commit_in(gone_path)
    gone.state = TicketState.DONE
    session.add(gone)
    session.commit()

    outside = _ticket(session, workspace, external_id="LG-OUTSIDE")
    outside_row = _out_of_base_row(session, workspace, outside, tmp_path)

    with caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"):
        assert reconcile_worktrees(session) == 1

    assert not gone_path.exists()
    session.refresh(outside_row)
    assert outside_row.state == WorktreeState.ACTIVE
    records = _lifecycle_records(caplog)
    assert len(records) == 1
    assert outside_row.id in records[0].getMessage()


def test_no_caller_outside_the_module_reads_the_retirement_reason_fields():
    """AC21 — the scope fence, as a test rather than a grep nobody re-runs.

    `removed` answers "is the checkout gone" and `kept_because` supplies the
    words; the two raw reason fields stay inside the module that sets them. A
    caller branching on `keep_reason is None` would re-create the exact collapse
    this ticket closes, because a failed removal has no keep reason either.
    """
    services = Path(worktree_lifecycle.__file__).parent
    lifecycle = Path(worktree_lifecycle.__file__).name
    readers = sorted(
        path
        for path in services.parent.rglob("*.py")
        if path.name != lifecycle and "worktree_lifecycle" in path.read_text()
    )
    assert readers, "expected to find the callers; the search is wrong, not the code"

    offenders = {
        path.name: [
            line.strip()
            for line in path.read_text().splitlines()
            if ".keep_reason" in line or ".failure" in line
        ]
        for path in readers
    }
    assert {name: lines for name, lines in offenders.items() if lines} == {}


# --------------------------------------------------------------------------
# The same failure without the mock, and the batch — lg-external-harness-954
#
# Every regression above forces `remove_checkout` to answer a member, which is
# the method AC20 prescribes and the only way to reach the distinction on a
# path a premise cannot refuse. It also means nothing above proves the real
# `remove_checkout` ever refuses, or that `retire_worktree` reads the
# real one the same way it reads the stub: a stub returning False and a
# function that raises inside and returns False look identical from here only
# if the seam is really the bool. These exercise the genuine failure, and the
# genuine failure mixed with a genuine success in one pass.
# --------------------------------------------------------------------------


@pytest.mark.skipif(
    hasattr(os, "geteuid") and os.geteuid() == 0,
    reason="root ignores the read-only parent, so the removal would succeed",
)
def test_a_checkout_the_filesystem_will_not_give_up_is_not_reported_released(session, workspace):
    """AC1/AC2 with no stub at the seam — the real `remove_checkout` refusing.

    `git worktree remove` cannot unlink the directory out of a read-only parent
    and neither can the `shutil.rmtree` fallback, so the real function takes its
    own `except Exception` branch and answers False: a permission error, which
    is one of the two failures this ticket is about. The checkout is still on
    disk and the row still says ACTIVE, so reporting the ticket's worktree
    released would be a claim contradicted by both.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    base = path.parent
    base.chmod(0o500)
    try:
        released = release_ticket_worktree(session, ticket)
    finally:
        base.chmod(0o700)

    assert released is False
    assert path.is_dir()
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value


def test_reconcile_counts_the_trees_that_went_and_names_only_the_one_that_did_not(
    session, workspace, caplog
):
    """AC19 at the volume reconcile actually runs at: more than one row.

    A boot settles every active row in one pass, so the count and the warning
    are per-tree answers, not per-run ones. With one failure beside one real
    removal, a `settled += 1` that ignores the bool reports 2, a blanket `else`
    warns about the tree that went as well, and both read as the other
    tree's story. The successful removal here is the real `remove_checkout`,
    so the pass is not uniformly stubbed.
    """
    gone = _ticket(session, workspace, external_id="LG-GONE")
    gone_path = _tree_for(session, workspace, gone)
    _commit_in(gone_path)
    stuck = _ticket(session, workspace, external_id="LG-STUCK")
    stuck_path = _tree_for(session, workspace, stuck)
    _commit_in(stuck_path)
    for ticket in (gone, stuck):
        ticket.state = TicketState.DONE
        session.add(ticket)
    session.commit()
    stuck_row = session.exec(select(Worktree).where(Worktree.ticket_id == stuck.id)).one()

    real_cleanup = WorktreeService.remove_checkout

    def refuse_only_the_stuck_one(self, worktree):
        # The member, never `False`: `None` is the settled answer now, and
        # `False` is falsy in exactly the place `None` is.
        if worktree.id == stuck_row.id:
            return CleanupRefusal.REMOVE_RAISED
        return real_cleanup(self, worktree)

    with (
        mock.patch.object(
            WorktreeService,
            "remove_checkout",
            autospec=True,
            side_effect=refuse_only_the_stuck_one,
        ),
        caplog.at_level(logging.WARNING, logger="loregarden.services.worktree_lifecycle"),
    ):
        assert reconcile_worktrees(session) == 1

    warnings = [
        record
        for record in caplog.records
        if record.levelno >= logging.WARNING
        and record.name == "loregarden.services.worktree_lifecycle"
    ]
    assert len(warnings) == 1
    message = warnings[0].getMessage()
    assert stuck_row.id in message
    assert str(stuck_path) in message
    assert str(gone_path) not in message
    assert not gone_path.exists()
    assert stuck_path.is_dir()


def test_a_failure_outranks_a_premise_when_an_answer_somehow_carries_both(session, workspace):
    """AC14 — `kept_because` is `failure or <the premise>`, in that order.

    No branch in `retire_worktree` sets both fields, which is exactly why the
    order has to be pinned here rather than left to a caller to discover: the
    inverted property (`the premise if it is set, else the failure`) passes
    every behavioural test in this module, and then the first caller to build a
    `Retirement` by hand — or the first branch that learns to carry a premise
    *and* a failure — reports a refusal that chose nothing about a removal that
    was attempted and failed.
    """
    outcome = Retirement(removed=False, keep_reason=KeepReason.UNCOMMITTED, failure=REMOVAL_FAILED)

    assert outcome.kept_because == REMOVAL_FAILED
    assert Retirement(removed=False, keep_reason=KeepReason.UNCOMMITTED).kept_because == (
        KeepReason.UNCOMMITTED.value
    )
    assert Retirement(removed=True).kept_because == ""


# --------------------------------------------------------------------------
# Adversarial: a failed removal is a retry, not a verdict
# — lg-external-harness-954, test-break
# --------------------------------------------------------------------------


def test_a_removal_that_failed_once_is_attempted_again_rather_than_remembered(session, workspace):
    """AC1/AC2 — the failure carries no memory, so the next attempt is a real one.

    Two causes sit behind that single False: a path outside the service's base
    (never retryable) and an exception from `git worktree remove` plus the
    `shutil.rmtree` fallback — a lock, a busy file, an editor holding a handle,
    usually transient. Nothing may cache the first answer, because the row
    staying ACTIVE is only a retry if something actually retries: the next boot
    for a ticket tree, the operator's second delete for a chat one.

    The tests above all ask one call one question. This is the only one that
    runs the failure and the recovery in one order, which is the only way a
    leftover from the first attempt — a stamped row, a short-circuit on
    `cleaned_at`, a memoized `Retirement` — is observable at all.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()

    with (
        mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None),
        mock.patch.object(
            WorktreeService,
            "remove_checkout",
            autospec=True,
            return_value=CleanupRefusal.REMOVE_RAISED,
        ) as refused,
    ):
        assert release_ticket_worktree(session, ticket) is False
        assert release_ticket_worktree(session, ticket) is False

    assert refused.call_count == 2
    assert _row(session, ticket).state == WorktreeState.ACTIVE.value

    # And once the path is removable the same call succeeds, with nothing left
    # over from the two failures standing in the way.
    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        assert release_ticket_worktree(session, ticket) is True
    assert not path.exists()
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value
