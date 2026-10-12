"""Worktrees are retired when the ticket ends, and after a crash.

The failure this closes is "server restart kills mid-run orchestration": the
row said active, the process that owned it was gone, and nothing ever looked
again. The counterpart rule is that a crash must not cost work — a tree with
uncommitted changes is kept, and a tree whose ticket is still unfinished is
left for the resume path.
"""

import logging
import subprocess
from pathlib import Path

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
from loregarden.services.ticket_worktree import resolve_execution_root
from loregarden.services.worktree_lifecycle import (
    KeepReason,
    reconcile_worktrees,
    release_ticket_worktree,
)
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
