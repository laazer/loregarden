"""Worktrees are retired when the ticket ends, and after a crash.

The failure this closes is "server restart kills mid-run orchestration": the
row said active, the process that owned it was gone, and nothing ever looked
again. The counterpart rule is that a crash must not cost work — a tree with
uncommitted changes is kept, and a tree whose ticket is still unfinished is
left for the resume path.
"""

import ast
import dataclasses
import logging
import os
import subprocess
from pathlib import Path
from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    ProcessState,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from loregarden.services import worktree_lifecycle
from loregarden.services.directory_release import DirectoryReleaseFailed, ReleaseDisposition
from loregarden.services.git_subprocess import run_git
from loregarden.services.process_identity import BootIdentityUnavailable
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import (
    KeepReason,
    RemovalFailure,
    Retirement,
    reconcile_worktrees,
    release_ticket_worktree,
    retire_worktree,
)
from loregarden.services.worktree_receipt import Unresolved
from loregarden.services.worktree_service import CleanupRefusal, WorktreeService
from sqlmodel import Session, select
from tests.worktree_helpers import git, grant_receipt, make_repo


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
    # lg-external-harness-1026: removing a checkout now has one more premise —
    # a recorded receipt that the work landed — so this fixture has to earn one.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # lg-external-harness-1026: removing a checkout now has one more premise —
    # a recorded receipt that the work landed — so this fixture has to earn one.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # Receipt-first (AC1): without one every tree below would be kept for
    # NO_RECEIPT, and the ordering *among the shape premises* — which is what
    # this test is about — would never be reached.
    grant_receipt(session, dirty_ticket, Path(workspace.repo_path))
    (dirty / "unsaved.txt").write_text("not committed\n")
    empty_ticket = _ticket(session, workspace, external_id="LG-2")
    empty = _tree_for(session, workspace, empty_ticket)
    grant_receipt(session, empty_ticket, Path(workspace.repo_path))
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
    # Receipt-first (AC1): without one every tree below would be kept for
    # NO_RECEIPT, and the ordering *among the shape premises* — which is what
    # this test is about — would never be reached.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    grant_receipt(session, committed_ticket, Path(workspace.repo_path))

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
    # Receipt-first (AC1): without one every tree below would be kept for
    # NO_RECEIPT, and the ordering *among the shape premises* — which is what
    # this test is about — would never be reached.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # lg-external-harness-1026: removing a checkout now has one more premise —
    # a recorded receipt that the work landed — so this fixture has to earn one.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # lg-external-harness-1026: removing a checkout now has one more premise —
    # a recorded receipt that the work landed — so this fixture has to earn one.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # lg-external-harness-1026 implement (AC18): the receipt is the FIRST premise, so a
    # fixture whose subject is a premise further down the chain has to earn one.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # lg-external-harness-1026 implement (AC18): the receipt is the FIRST premise, so a
    # fixture whose subject is a premise further down the chain has to earn one.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # Receipt-first (AC1): without one every tree below would be kept for
    # NO_RECEIPT, and the ordering *among the shape premises* — which is what
    # this test is about — would never be reached.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # lg-external-harness-1026: removing a checkout now has one more premise —
    # a recorded receipt that the work landed — so this fixture has to earn one.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # assertion below are 951's. What separates a premise that *chose* to keep
    # the tree from a removal that was tried and failed is now the refusal's own
    # type — a `KeepReason` here, never `RemovalFailure` — and the empty
    # `parked_path`, because nothing was moved. Collapsing those two was
    # lg-external-harness-954.
    assert outcome.refusal is reason
    assert outcome.deleted_nothing is True
    assert outcome.parked_path == ""
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

    # `refusal is None` is the whole answer now (lg-external-harness-1026 AC12,
    # AC18): `removed` and `keep_reason` are deleted rather than kept beside it,
    # because a second signal is the dual path AC12 forbids. Same claim, read
    # off the one field that carries it.
    assert outcome.refusal is None
    assert outcome.deleted_nothing is False
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

#: The clause `retire_worktree` reports a retryable failed removal with.
#: Carried by the enum member rather than a module constant since
#: lg-external-harness-1026 — `RemovalFailure` is the vocabulary and a second
#: spelling of its value is a second thing to keep in step with it — and its
#: value stays byte-identical to what lg-external-harness-954 measured against
#: the four-line toast clamp, which lg-external-harness-1165 AC2/AC10 keep
#: valid. The constant below asserts that identity rather than restating it.
REMOVAL_FAILED = RemovalFailure.CLEANUP_FAILED.value

#: Where `release_directory` parks a checkout it is about to delete. Every
#: failed removal below carries one, because the rename happens before the
#: delete that fails.
PARKED_SUFFIX = ".parked"

#: Its never-retryable counterpart (lg-external-harness-1165 AC2). A different
#: clause for a different cause: "removing its checkout failed" invites a retry,
#: and no retry can move a checkout the service will not touch.
REMOVAL_REFUSED_OUTSIDE_BASE = "its checkout is outside the worktrees folder"

#: Which clause each seam refusal is reported with. Keyed on
#: `CleanupRefusal`, not on `RemovalFailure`: lg-external-harness-1026 answers
#: both causes with the one member `CLEANUP_FAILED` — a caller branching per
#: cause is the dual path AC12 forbids — so the member a caller reads is the
#: same and the *detail* is what still tells an operator which of the two
#: happened. Mirrors the mapping `retire_worktree` reads, so a `CleanupRefusal`
#: added without a clause fails the parametrized test below rather than
#: silently inheriting a sentence written for the other cause.
FAILURE_CLAUSE = {
    "REMOVE_RAISED": REMOVAL_FAILED,
    "OUTSIDE_BASE": REMOVAL_REFUSED_OUTSIDE_BASE,
}


def _failing_service(
    path: Path | str = "/nowhere",
    refusal: CleanupRefusal = CleanupRefusal.REMOVE_RAISED,
) -> mock.MagicMock:
    """A seam that fails the way the real one now does: by raising.

    Two shapes died here and the resolution keeps what each bought.
    `cleanup_worktree` returning False had no room for a cause;
    `remove_checkout` returning a `CleanupRefusal` had room for the cause but
    not for the parked path, and left "it failed" as a value a caller could
    forget to read. The seam keeps lg-external-harness-1165's name and raises
    `DirectoryReleaseFailed` carrying both — the member and the path — and
    `retire_worktree` is the one place that catches it.

    A containment refusal parks nothing, because nothing was attempted, so it
    carries an empty `parked_path` rather than a path no rename produced.
    """
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)
    parked = f"{path}{PARKED_SUFFIX}" if refusal is CleanupRefusal.REMOVE_RAISED else ""
    service.remove_checkout.side_effect = DirectoryReleaseFailed(
        "rmtree failed",
        disposition=ReleaseDisposition.STILL_PARKED if parked else ReleaseDisposition.INTACT,
        refusal=refusal,
        uncleaned_path=str(path),
        parked_path=parked,
    )
    return service


def test_a_removal_that_was_tried_and_failed_is_not_reported_as_a_removal(session, workspace):
    """AC1/AC14 — the checkout is still there, and the answer says so.

    Every premise held, so no `KeepReason` fired: nothing *chose* to keep this
    tree. `refusal` is still not None, because the removal did not happen, and
    its being a `RemovalFailure` rather than a `KeepReason` is what tells the
    two apart — the collapse `refusal is None` would otherwise hide.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = _failing_service(path)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    service.remove_checkout.assert_called_once_with(row)
    assert outcome.refusal is RemovalFailure.CLEANUP_FAILED
    assert outcome.refusal not in set(KeepReason)
    assert outcome.deleted_nothing is True
    assert REMOVAL_FAILED in outcome.detail
    assert outcome.parked_path == f"{path}{PARKED_SUFFIX}"
    assert path.is_dir()


def test_a_successful_removal_has_nothing_to_say_about_why_it_kept_the_tree(session, workspace):
    """AC14 — `detail` is words for a caller to print, and on the happy path
    there is nothing to park and nothing to report. A caller that printed a
    parked path here would be announcing a refusal that never happened."""
    ticket = _ticket(session, workspace)
    _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    assert outcome.refusal is None
    assert outcome.deleted_nothing is False
    assert outcome.parked_path == ""


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
    service = _failing_service(path)

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
    """AC12 — the outcome is this function's own decision, not the seam's answer.

    A `MagicMock(spec=...)` with no `side_effect` set does not raise, which is
    the seam's success, and answers with a truthy `Mock`. Passing that through
    as a refusal — or coercing it into one — is the shape that makes the field
    mean "whatever `remove_checkout` said" rather than "the checkout is gone",
    and 951's own tests pass exactly this mock, so a passthrough would leak a
    `Mock` where a caller reads `refusal is None`.

    What this cannot catch, verified by mutation rather than assumed: a
    `bool(...)`/`not ...` coercion of a value that is already truthy survives
    it, because the coercion is unobservable. AC12 reads as though this
    assertion forbids the coercion; it does not, and no behavioural test can.
    That is a code-shape rule — defensive normalization of a value whose type is
    already known — so it belongs to the organization gate and the reviewer.
    """
    ticket = _ticket(session, workspace)
    _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    assert outcome.refusal is None
    assert outcome.deleted_nothing is False
    assert outcome.parked_path == ""


@pytest.mark.parametrize("refusal", list(CleanupRefusal), ids=lambda r: r.name)
def test_each_seam_refusal_is_reported_with_its_own_clause(session, workspace, refusal):
    """AC13 — the two causes answer one member and do not share a sentence.

    lg-external-harness-1165 gave the seam a `CleanupRefusal` so a caller could
    pick words without parsing prose, and 1026 answers every failed removal
    with the single member `RemovalFailure.CLEANUP_FAILED`, because a caller
    branching per cause is the dual path AC12 forbids. Both survive here: the
    *member* a caller reads is the same for either cause, and the *detail* is
    what still tells an operator which happened — "removing its checkout
    failed" invites the retry that usually clears it, and no retry can move a
    checkout outside the worktrees folder.

    The vocabulary, not a list of branches: parametrizing over `CleanupRefusal`
    means the cause added after these two cannot quietly inherit whichever
    clause the mapping happens to default to.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = _failing_service(path, refusal)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    service.remove_checkout.assert_called_once_with(row)
    assert outcome.refusal is RemovalFailure.CLEANUP_FAILED
    assert outcome.deleted_nothing is True
    assert FAILURE_CLAUSE[refusal.name] in outcome.detail
    # The other cause's clause is not also in it: one sentence per cause, which
    # is what a shared default would quietly break.
    others = {clause for name, clause in FAILURE_CLAUSE.items() if name != refusal.name}
    assert not any(clause in outcome.detail for clause in others)
    assert path.is_dir()


def test_the_two_failure_clauses_stay_distinct_and_the_measured_one_unmoved():
    """AC13 — two causes, two clauses, and the measured string byte-for-byte.

    `RemovalFailure.CLEANUP_FAILED.value` is what lg-external-harness-954
    measured against the four-line toast clamp, so the retryable path keeps
    that evidence even though the clause moved into the enum. The
    never-retryable clause is a second string rather than a reworded first one,
    because collapsing them is the defect restated one layer up.
    """
    assert RemovalFailure.CLEANUP_FAILED.value == "removing its checkout failed"
    assert (
        worktree_lifecycle.REMOVAL_REFUSED_OUTSIDE_BASE
        == "its checkout is outside the worktrees folder"
    )
    assert len(set(FAILURE_CLAUSE.values())) == len(FAILURE_CLAUSE)
    assert set(FAILURE_CLAUSE) == {member.name for member in CleanupRefusal}


def test_a_containment_refusal_parks_nothing_and_says_nothing_about_a_park(session, workspace):
    """AC9/AC13 — nothing was renamed, so no path is named.

    `OUTSIDE_BASE` is decided before the `os.replace`, so there is no parked
    copy to report. An outcome naming one would send an operator looking for a
    directory that was never created — the mirror of the defect where a refusal
    failed to name the directory that was.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    row = session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()
    service = _failing_service(path, CleanupRefusal.OUTSIDE_BASE)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        outcome = retire_worktree(session, service, row)

    assert outcome.refusal is RemovalFailure.CLEANUP_FAILED
    assert outcome.parked_path == ""
    assert PARKED_SUFFIX not in outcome.detail
    assert path.is_dir()


def test_the_failure_clause_is_one_module_constant(session, workspace):
    """AC13 — the clause is defined once, where both branches and any future
    caller read the same string. Spelled inline at each use it drifts, and a
    test pinning one spelling then passes while the other has changed.

    lg-external-harness-1026 moved it into `RemovalFailure`, so the rule is now
    that nothing *beside* the enum holds the same string: a module constant
    shadowing a member value is the drift this guards against, one indirection
    further out.
    """
    shadows = [
        name
        for name in dir(worktree_lifecycle)
        if not name.startswith("__")
        and name not in {"RemovalFailure", "RetirementRefusal"}
        and isinstance(getattr(worktree_lifecycle, name, None), str)
        and getattr(worktree_lifecycle, name) == REMOVAL_FAILED
    ]
    assert shadows == [], f"the clause is spelled a second time outside the enum: {shadows}"


def test_a_retirement_cannot_be_rewritten_after_the_fact(session, workspace):
    """AC14 — frozen, because this travels to a caller that decides whether to
    delete a branch. A mutable answer is one an intermediate layer can
    "normalize" into the success it was supposed to prevent."""
    outcome = Retirement(
        refusal=RemovalFailure.CLEANUP_FAILED,
        deleted_nothing=True,
        parked_path="/nowhere.parked",
        detail="nothing was deleted",
    )

    with pytest.raises(dataclasses.FrozenInstanceError):
        outcome.refusal = None  # type: ignore[misc]


def test_the_premise_vocabulary_is_closed_and_the_failure_stays_alone():
    """AC11 — every premise is a `KeepReason`; `RemovalFailure` keeps one member.

    954 pinned three premises and read a fourth member as a bug. 1026 adds five,
    and the rule it kept is the one that mattered: a premise *chose* to keep the
    tree, and `RemovalFailure` means every premise held and the removal did not
    work. A premise filed under `RemovalFailure` would be reported as a failure
    an operator cannot act on; a failure filed under `KeepReason` would be
    grouped by `_kept_summary` as though something had decided it.
    """
    assert [reason.name for reason in KeepReason] == [
        "UNCOMMITTED",
        "STRANDED_HEAD",
        "NOTHING_COMMITTED",
        "NO_RECEIPT",
        "RECEIPT_DOES_NOT_COVER",
        "CLAIM_UNPROVEN",
        "UNREADABLE",
        "WORKSPACE_GONE",
        "CLAIMANT_LIVE",
        "PARKED_CHECKOUT",
    ]
    assert [member.name for member in RemovalFailure] == ["CLEANUP_FAILED"]


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
            WorktreeService,
            "remove_checkout",
            side_effect=DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(path),
                parked_path=f"{path}{PARKED_SUFFIX}",
            ),
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
    """AC19 — reconcile's first direct seam call stops being discarded.

    The directory is gone, so there is no work to lose and the row is stale
    bookkeeping — but if stamping it CLEANUP fails, the row is still ACTIVE and
    the count must not claim otherwise. Counting it anyway reports "reconciled
    1" every boot for a row that never moves, which is the leaked-row failure
    this module exists to close, reported as its own fix.

    The seam this path calls is `settle_absent_checkout`, not `remove_checkout`
    (lg-external-harness-1026 AC9): an absence the gate OBSERVED may not
    license a rename-then-delete of whatever the recorded path holds by the
    time the release runs, so the vanished-row arm judges containment and
    stamps the row with the destructive half left out. Same claim, same
    assertion — the failing call is the one this arm actually makes.
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
            WorktreeService,
            "settle_absent_checkout",
            side_effect=DirectoryReleaseFailed(
                "stamping the row failed",
                disposition=ReleaseDisposition.RESTORED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(path),
                parked_path="",
            ),
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
            WorktreeService,
            "remove_checkout",
            side_effect=DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(path),
                parked_path=f"{path}{PARKED_SUFFIX}",
            ),
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
            # Raised, not returned (lg-external-harness-1026 AC14, AC18): the
            # seam is a command, and a value a caller can forget to read is the
            # defect one shape along from the bool.
            raise DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(stuck_path),
                parked_path=f"{stuck_path}{PARKED_SUFFIX}",
            )
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
    # lg-external-harness-1026 implement (AC18): the receipt is the FIRST premise, so a
    # fixture whose subject is a premise further down the chain has to earn one.
    grant_receipt(session, gone, Path(workspace.repo_path))
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

    `refusal is None` answers "is the checkout gone", `detail` supplies the
    words and `parked_path` says where it went. 954's two raw reason fields are
    gone, and nothing may reintroduce a reader for them: a caller branching on
    `keep_reason is None` re-creates the exact collapse these tickets close,
    because a failed removal has no keep reason either.
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
    # lg-external-harness-1026: the receipt is the first premise, so a real
    # `_keep_reason` refuses before the filesystem is ever asked.
    grant_receipt(session, ticket, Path(workspace.repo_path))
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
    # lg-external-harness-1026: both trees reach a real `_keep_reason`, whose
    # first premise is the receipt.
    grant_receipt(session, gone, Path(workspace.repo_path))
    grant_receipt(session, stuck, Path(workspace.repo_path))
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
            raise DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(stuck_path),
                parked_path=f"{stuck_path}{PARKED_SUFFIX}",
            )
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


def test_an_answer_cannot_carry_a_premise_and_a_failure_at_once(session, workspace):
    """AC12 — 954 pinned `kept_because == failure or <the premise>`; 1026 deletes
    the question by deleting the second field.

    That ordering existed because two fields could both be set and a caller
    reading the wrong one would report a refusal that chose nothing about a
    removal that was attempted and failed. One `refusal` field cannot hold both,
    so the ordering is unrepresentable rather than merely correct — which is the
    stronger property, and the reason this test replaces that one rather than
    being dropped. What is left to pin is that the two vocabularies stay
    disjoint, because the whole collapse rides on telling them apart.
    """
    premise = Retirement(
        refusal=KeepReason.UNCOMMITTED,
        deleted_nothing=True,
        parked_path="",
        detail="nothing was deleted",
    )
    failure = Retirement(
        refusal=RemovalFailure.CLEANUP_FAILED,
        deleted_nothing=True,
        parked_path="/nowhere.parked",
        detail="nothing was deleted",
    )

    assert premise.refusal in set(KeepReason)
    assert premise.refusal not in set(RemovalFailure)
    assert failure.refusal in set(RemovalFailure)
    assert failure.refusal not in set(KeepReason)
    assert set(KeepReason).isdisjoint(set(RemovalFailure))
    assert not hasattr(premise, "kept_because")


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
            side_effect=DirectoryReleaseFailed(
                "rmtree failed",
                disposition=ReleaseDisposition.STILL_PARKED,
                refusal=CleanupRefusal.REMOVE_RAISED,
                uncleaned_path=str(path),
                parked_path=f"{path}{PARKED_SUFFIX}",
            ),
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


# ===========================================================================
# lg-external-harness-1026 — the receipt gate, the carried answer, and
# uncertainty that keeps ownership.
# ===========================================================================


def _active_row(session, ticket) -> Worktree:
    return session.exec(select(Worktree).where(Worktree.ticket_id == ticket.id)).one()


def _service(session, workspace):
    return worktree_lifecycle._service_for(session, workspace.id)


def _finished(session, ticket):
    ticket.state = TicketState.DONE
    session.add(ticket)
    session.commit()
    return ticket


# ---------------------------------------------------------------------------
# AC1 — the receipt is consulted FIRST, before any premise about shape
# ---------------------------------------------------------------------------


def test_a_tree_with_no_receipt_is_refused_even_when_every_shape_premise_holds(session, workspace):
    """The ticket's subject, in one test.

    Committed, clean, a branch ahead of its base, HEAD on the branch — every
    observation of *shape* says this checkout is safe to delete, and that is
    exactly the inference the surveyed harness never makes. Nothing on record
    says the work finished, so the checkout stays.
    """
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)

    outcome = retire_worktree(session, _service(session, workspace), _active_row(session, ticket))

    assert outcome.refusal is KeepReason.NO_RECEIPT
    assert outcome.deleted_nothing is True
    assert path.is_dir()
    assert (path / "work.txt").exists()


def test_the_receipt_is_asked_before_the_shape_premises_are(session, workspace):
    """Order, asserted where it is observable.

    A tree that is dirty *and* unlanded fails two premises. The receipt is the
    one consulted first, so it is the one reported — and the shape premises are
    never even run, because an unlanded tree's dirt is not what is keeping it.
    """
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    (path / "unsaved.txt").write_text("not committed\n")

    with mock.patch.object(worktree_lifecycle, "_is_dirty") as dirty_probe:
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert outcome.refusal is KeepReason.NO_RECEIPT
    dirty_probe.assert_not_called()


def test_a_landed_tree_that_every_premise_clears_is_removed(session, workspace):
    """The other direction, so the gate is not simply "refuse everything"."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))
    _finished(session, ticket)

    outcome = retire_worktree(session, _service(session, workspace), _active_row(session, ticket))

    assert outcome.refusal is None
    assert outcome.deleted_nothing is False
    assert outcome.parked_path == ""
    assert not path.exists()


def test_a_row_whose_directory_is_already_gone_is_settled_without_a_receipt(session, workspace):
    """The degenerate case, and the one place "receipt first" must not mean
    "receipt before everything".

    There is no directory, so there is no destructive act to gate: settling the
    row destroys nothing. Refusing here would pin every leaked row at ACTIVE
    forever — the exact leak this module was written to close, re-opened by its
    own safety bar. The absence is established by `directory_state`, not by a
    premise about what the tree contains.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    subprocess.run(
        ["git", "worktree", "remove", "--force", str(path)],
        cwd=session.get(Workspace, workspace.id).repo_path,
        check=True,
        capture_output=True,
    )
    _finished(session, ticket)
    assert not path.exists()
    assert not ticket.landed_sha

    outcome = retire_worktree(session, _service(session, workspace), _active_row(session, ticket))

    assert outcome.refusal is None
    assert _row(session, ticket).state == WorktreeState.CLEANUP.value


# ---------------------------------------------------------------------------
# AC7 — the fail-open on an absent claim is bounded by the receipt
# ---------------------------------------------------------------------------


def test_an_empty_claim_with_no_receipt_is_still_refused(session, workspace):
    """The bound AC7 requires, written as the test it names.

    Reading an empty claim as "no claimant" is the one deliberate fail-open in
    this change, and on its own it would hand every pre-migration checkout to
    the reaper. It does not, because the claim is not the only premise: no
    receipt, no removal, whoever does or does not own the tree.
    """
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    row = _active_row(session, ticket)
    row.claim_pid = ""
    row.claim_birth = ""
    row.claim_boot_id = ""
    session.add(row)
    session.commit()

    outcome = retire_worktree(session, _service(session, workspace), row)

    assert outcome.refusal is KeepReason.NO_RECEIPT
    assert path.is_dir()


def test_a_tree_a_live_claimant_still_holds_is_kept_even_with_a_receipt(session, workspace):
    """A receipt says the work finished. A live claimant says somebody is still
    in there. Both are premises and both must hold."""
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))
    _finished(session, ticket)
    # The claim has to name ANOTHER process (lg-external-harness-1026
    # implement, AC18): a tree is claimed by the process that cut it, and that
    # is this one, which also retires it — so a self-claim is deliberately not
    # a live claimant, or no server would ever release a tree it had cut. The
    # assertion below is unchanged; only the fixture now describes the case it
    # is named for.
    foreign = _active_row(session, ticket)
    foreign.claim_pid = "99999"
    session.add(foreign)
    session.commit()

    with mock.patch.object(worktree_lifecycle, "claim_is_live", return_value=ProcessState.ALIVE):
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert outcome.refusal is KeepReason.CLAIMANT_LIVE
    assert path.is_dir()


# ---------------------------------------------------------------------------
# AC11 — uncertainty keeps ownership, and records itself
# ---------------------------------------------------------------------------


def test_the_new_premises_are_all_keep_reasons_and_removal_failure_stays_alone(session):
    """The vocabulary split, asserted rather than reviewed.

    `KeepReason` is premises. `RemovalFailure` means one thing and must keep
    meaning only that: every premise held and the removal did not work. A new
    premise filed under `RemovalFailure` would be grouped, reported and acted
    on as a broken disk.
    """
    members = {member.name for member in KeepReason}
    assert {
        "NO_RECEIPT",
        "RECEIPT_DOES_NOT_COVER",
        "CLAIM_UNPROVEN",
        "UNREADABLE",
        "CLAIMANT_LIVE",
    } <= members
    assert [member.name for member in RemovalFailure] == ["CLEANUP_FAILED"]


def test_a_stat_the_kernel_refuses_is_a_refusal_and_not_an_absence(session, workspace):
    """The crash-in-a-sweep failure, and the one it must not become.

    `directory_state` raises on EACCES rather than answering. At the decision
    site that becomes UNREADABLE: the tree is still owned, nothing is deleted,
    and the row records *why* nobody could tell. The two wrong answers are
    "absent" (which deletes) and an uncaught raise (which ends the sweep).
    """
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))

    with mock.patch.object(
        worktree_lifecycle,
        "directory_state",
        side_effect=PermissionError(13, "Permission denied"),
    ):
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert outcome.refusal is KeepReason.UNREADABLE
    assert outcome.deleted_nothing is True
    assert path.is_dir()
    assert _active_row(session, ticket).unresolved_reason


def test_a_boot_stamp_that_cannot_be_read_is_a_refusal_not_a_free_pass(session, workspace):
    """`BootIdentityUnavailable` is caught here and nowhere else.

    Catching it inside `process_identity` would mean returning a value, and
    every value available is one an unset column compares equal to.
    """
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))

    with mock.patch.object(
        worktree_lifecycle, "claim_is_live", side_effect=BootIdentityUnavailable("no sysctl")
    ):
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert outcome.refusal is KeepReason.CLAIM_UNPROVEN
    assert path.is_dir()
    assert _active_row(session, ticket).unresolved_reason


@pytest.mark.parametrize(
    "broken",
    [
        PermissionError(13, "Permission denied"),
        OSError(5, "Input/output error"),
        BootIdentityUnavailable("no sysctl"),
    ],
    ids=["eacces", "eio", "no-boot-stamp"],
)
def test_no_uncertainty_is_ever_answered_with_false_or_none(session, workspace, broken):
    """The rule, over every way of not knowing.

    "I could not find out" must never arrive as `False` or `None`, because both
    of those already mean something else here — and `None` in particular means
    *the checkout is gone*.
    """
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))

    target = "claim_is_live" if isinstance(broken, BootIdentityUnavailable) else "directory_state"
    with mock.patch.object(worktree_lifecycle, target, side_effect=broken):
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert outcome.refusal is not None
    assert outcome.refusal is not False
    assert outcome.deleted_nothing is True
    assert path.is_dir()


def test_one_unreadable_tree_does_not_end_the_sweep(session, workspace):
    """An EACCES raised mid-loop must not cost the rest of the pass.

    This is the failure the stat helper actually buys (the spec measured that
    `pathlib` never swallowed EACCES in the first place): a raise out of
    `directory_state` inside `reconcile_worktrees` would abort the loop, and
    every worktree after the unreadable one would go unvisited — silently,
    because an aborted sweep and a clean sweep both end with no error.
    """
    unreadable_ticket = _ticket(session, workspace, external_id="LG-LOCKED")
    unreadable = _tree_for(session, workspace, unreadable_ticket)
    _commit_in(unreadable)
    grant_receipt(session, unreadable_ticket, Path(workspace.repo_path))

    retirable_ticket = _ticket(session, workspace, external_id="LG-OK")
    retirable = _tree_for(session, workspace, retirable_ticket)
    # A file of its own: the first tree's `work.txt` is in `main` by now (its
    # receipt landed it), so committing the same name and contents here is
    # "nothing to commit" and the fixture fails rather than the assertion.
    _commit_in(retirable, "retirable.txt")
    grant_receipt(session, retirable_ticket, Path(workspace.repo_path))

    for ticket in (unreadable_ticket, retirable_ticket):
        _finished(session, ticket)

    real_state = worktree_lifecycle.directory_state

    def refuse_the_locked_one(path, *args, **kwargs):
        if str(path) == str(unreadable):
            raise PermissionError(13, "Permission denied")
        return real_state(path, *args, **kwargs)

    with mock.patch.object(
        worktree_lifecycle, "directory_state", side_effect=refuse_the_locked_one
    ):
        settled = reconcile_worktrees(session)

    assert settled == 1, "the sweep kept going past the tree it could not read"
    assert unreadable.is_dir()
    assert not retirable.exists()
    assert _active_row(session, unreadable_ticket).unresolved_reason


def test_a_receipt_probe_whose_git_call_failed_refuses_and_records_it(session, workspace):
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))

    with mock.patch.object(
        worktree_lifecycle,
        "resolve_receipt",
        return_value=Unresolved(reason=KeepReason.NO_RECEIPT, detail="git could not be run"),
    ):
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert outcome.refusal is KeepReason.NO_RECEIPT
    assert path.is_dir()
    assert "git could not be run" in _active_row(session, ticket).unresolved_reason


# ---------------------------------------------------------------------------
# AC12 — the answer carries the parked path, and is never a coerced bool
# ---------------------------------------------------------------------------


def test_the_outcome_is_frozen_and_refusal_is_none_means_only_that_it_is_gone(session, workspace):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))
    _finished(session, ticket)

    outcome = retire_worktree(session, _service(session, workspace), _active_row(session, ticket))

    assert isinstance(outcome, Retirement)
    with pytest.raises(Exception):
        outcome.refusal = KeepReason.UNCOMMITTED
    assert outcome.refusal is None
    assert not path.exists(), "refusal is None must mean the checkout is gone, and only that"


def test_a_failed_removal_answers_cleanup_failed_with_the_parked_path(session, workspace):
    """Never `refusal=None`, and never without saying where the bytes went.

    The parked path is the whole recovery procedure for an operator: the
    directory is not where its row says it is, and only this string knows where
    it is instead.
    """
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))
    _finished(session, ticket)
    parked = f"{path}.parked-deadbeef"

    with mock.patch.object(
        worktree_lifecycle.WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED if parked else ReleaseDisposition.INTACT,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(path),
            parked_path=parked,
        ),
    ):
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert outcome.refusal is RemovalFailure.CLEANUP_FAILED
    assert outcome.refusal is not None
    assert outcome.deleted_nothing is True
    assert outcome.parked_path == parked
    assert _active_row(session, ticket).parked_at == parked
    assert _active_row(session, ticket).unresolved_reason


def test_the_outcome_is_built_by_branching_and_never_by_coercing_a_truthy_value(session, workspace):
    """A `Mock()` is truthy, and so is every object a service could return.

    `deleted_nothing=bool(not removed)` passes every behavioural test above and
    silently becomes wrong the day the removal path returns something other
    than a bool. The outcome is constructed from which branch ran, so a truthy
    stand-in cannot make a refusal look like a success.
    """
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    row = _active_row(session, ticket)
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)
    service.remove_checkout.return_value = mock.Mock()  # truthy, and meaningless

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=KeepReason.NO_RECEIPT):
        refused = retire_worktree(session, service, row)
    assert refused.refusal is KeepReason.NO_RECEIPT
    assert refused.deleted_nothing is True

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=None):
        removed = retire_worktree(session, service, row)
    assert removed.refusal is None
    assert removed.deleted_nothing is False
    assert path.is_dir()  # the stub removed nothing; the branch is the assertion


def test_there_is_no_force_or_override_or_receipts_off_switch_anywhere(session):
    """The forbidden dual code path, and a `--force` on a safety bar in one.

    A gate with a way around it is a gate nobody has to argue with. The first
    time a sweep is inconvenient, the switch is what gets used, and the defect
    this ticket closes comes back wearing a flag.
    """
    import inspect

    from loregarden.services import directory_release, worktree_receipt

    for module in (worktree_lifecycle, worktree_receipt, directory_release):
        source = inspect.getsource(module)
        for banned in (
            "force=",
            "skip_receipt",
            "ignore_receipt",
            "receipts_off",
            "no_receipt_check",
        ):
            assert banned not in source, f"{module.__name__} offers a way past the gate: {banned}"

    for name in ("retire_worktree", "release_ticket_worktree", "release_chat_worktree"):
        parameters = inspect.signature(getattr(worktree_lifecycle, name)).parameters
        assert not any("force" in p or "override" in p or "receipt" in p for p in parameters), (
            f"{name} takes an argument that could turn the gate off: {list(parameters)}"
        )


# ---------------------------------------------------------------------------
# AC13 — every refusal says what it did not delete, and names what it parked
# ---------------------------------------------------------------------------


@pytest.mark.parametrize("reason", list(KeepReason), ids=lambda r: r.name)
def test_every_keep_reason_s_returned_detail_says_nothing_was_deleted(session, workspace, reason):
    """Asserted on the RETURNED message, never on a log record and never on a
    docstring. The operator reads the answer, not the source."""
    ticket = _finished(session, _ticket(session, workspace))
    path = _tree_for(session, workspace, ticket)
    row = _active_row(session, ticket)
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=reason):
        outcome = retire_worktree(session, service, row)

    assert outcome.detail
    lowered = outcome.detail.lower()
    assert "nothing" in lowered and "delet" in lowered, (
        f"{reason.name} does not say nothing was deleted: {outcome.detail!r}"
    )
    assert str(path) in outcome.detail, f"{reason.name} does not name the tree it kept"


def test_the_no_receipt_detail_names_both_verbs_that_write_a_receipt(session, workspace):
    """The operator's only exit from a tree the gate will otherwise keep forever.

    A tree whose ticket reached a terminal state without landing or publishing
    never earns a receipt on its own. That refusal *is* this family's fix
    (lg-external-harness-489) — and it is only humane if the message says which
    two verbs end it.
    """
    ticket = _finished(session, _ticket(session, workspace))
    _tree_for(session, workspace, ticket)
    row = _active_row(session, ticket)
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)

    with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=KeepReason.NO_RECEIPT):
        detail = retire_worktree(session, service, row).detail

    lowered = detail.lower()
    assert "land" in lowered, "the refusal does not name landing the branch"
    assert "publish" in lowered, "the refusal does not name publishing the tree"


def test_each_member_s_detail_is_its_own_and_repeats_no_neighbour_s_sentence(session, workspace):
    """One shared sentence for five reasons is five reasons nobody can tell
    apart, which is the state this gate replaces."""
    ticket = _finished(session, _ticket(session, workspace))
    _tree_for(session, workspace, ticket)
    row = _active_row(session, ticket)
    service = mock.MagicMock(spec=worktree_lifecycle.WorktreeService)

    details = {}
    for reason in list(KeepReason) + list(RemovalFailure):
        with mock.patch.object(worktree_lifecycle, "_keep_reason", return_value=reason):
            details[reason.name] = retire_worktree(session, service, row).detail

    assert len(set(details.values())) == len(details), f"duplicated wording: {details}"


def test_an_outcome_with_a_parked_path_names_it_and_says_the_checkout_is_still_owned(
    session, workspace
):
    ticket = _ticket(session, workspace)
    path = _tree_for(session, workspace, ticket)
    _commit_in(path)
    grant_receipt(session, ticket, Path(workspace.repo_path))
    _finished(session, ticket)
    parked = f"{path}.parked-cafebabe"

    with mock.patch.object(
        worktree_lifecycle.WorktreeService,
        "remove_checkout",
        side_effect=DirectoryReleaseFailed(
            "rmtree failed",
            disposition=ReleaseDisposition.STILL_PARKED if parked else ReleaseDisposition.INTACT,
            refusal=CleanupRefusal.REMOVE_RAISED,
            uncleaned_path=str(path),
            parked_path=parked,
        ),
    ):
        outcome = retire_worktree(
            session, _service(session, workspace), _active_row(session, ticket)
        )

    assert parked in outcome.detail
    assert "own" in outcome.detail.lower()


# ---------------------------------------------------------------------------
# AC14.5 — every parked_at store is the live-path writer or the vanished clearer
# ---------------------------------------------------------------------------

_PACKAGE = Path(__file__).resolve().parents[1] / "loregarden"
_LIVE_PATH_WRITER = ("services/worktree_lifecycle.py", "_record_refusal")
_CLEARER = ("services/stale_parked_at_rows.py", "clear_vanished_parked_at")


def _parked_at_targets(target: ast.AST) -> list[ast.Attribute]:
    if isinstance(target, ast.Attribute) and target.attr == "parked_at":
        return [target]
    if isinstance(target, (ast.Tuple, ast.List)):
        found: list[ast.Attribute] = []
        for elt in target.elts:
            found.extend(_parked_at_targets(elt))
        return found
    return []


def _enclosing_function(tree: ast.AST, lineno: int) -> str | None:
    inner: ast.FunctionDef | ast.AsyncFunctionDef | None = None
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        end = node.end_lineno or node.lineno
        if node.lineno <= lineno <= end and (inner is None or node.lineno >= inner.lineno):
            inner = node
    return None if inner is None else inner.name


def _is_outcome_parked_path(value: ast.AST | None) -> bool:
    return (
        isinstance(value, ast.Attribute)
        and value.attr == "parked_path"
        and isinstance(value.value, ast.Name)
        and value.value.id == "outcome"
    )


def _is_empty_string(value: ast.AST | None) -> bool:
    return isinstance(value, ast.Constant) and value.value == ""


def _parked_at_stores() -> list[tuple[str, str | None, ast.AST | None]]:
    """Every `*.parked_at =` under server/loregarden, except class-field AnnAssign."""
    found: list[tuple[str, str | None, ast.AST | None]] = []
    for path in sorted(_PACKAGE.rglob("*.py")):
        relative = path.relative_to(_PACKAGE).as_posix()
        if relative.startswith("db/versions/"):
            continue
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=relative)
        class_ann = {
            id(stmt)
            for node in ast.walk(tree)
            if isinstance(node, ast.ClassDef)
            for stmt in node.body
            if isinstance(stmt, ast.AnnAssign)
        }
        for node in ast.walk(tree):
            value: ast.AST | None
            targets: list[ast.Attribute]
            if isinstance(node, ast.Assign):
                targets = [t for target in node.targets for t in _parked_at_targets(target)]
                value = node.value
            elif isinstance(node, ast.AnnAssign):
                if id(node) in class_ann:
                    continue
                targets = _parked_at_targets(node.target)
                value = node.value
            elif isinstance(node, ast.AugAssign):
                targets = _parked_at_targets(node.target)
                value = node.value
            else:
                continue
            if not targets:
                continue
            found.append((relative, _enclosing_function(tree, node.lineno), value))
    return found


def test_every_writer_of_parked_at_sets_it_only_from_an_unrestored_release():
    """AST census of writers, not a path test. A planted assignment in any
    other module fails. Allow-list: `_record_refusal` copies `outcome.parked_path`
    (the only setter of a live path) and `clear_vanished_parked_at` writes "".
    """
    stores = _parked_at_stores()
    allowed: set[tuple[str, str | None]] = set()
    offenders: list[str] = []
    for relative, func, value in stores:
        live = (relative, func) == _LIVE_PATH_WRITER and _is_outcome_parked_path(value)
        clearer = (relative, func) == _CLEARER and _is_empty_string(value)
        if live or clearer:
            allowed.add((relative, func))
            continue
        rhs = ast.unparse(value) if value is not None else "<empty>"
        offenders.append(f"{relative}:{func}:{rhs}")
    assert not offenders, (
        f"parked_at is written outside the live-path writer and the vanished clearer: {offenders}"
    )
    assert allowed == {_LIVE_PATH_WRITER, _CLEARER}, (
        f"both allow-listed sites must exist; found {sorted(allowed)}"
    )
