"""An ACTIVE `worktrees` row whose recorded checkout is gone, on the timer.

lg-external-harness-1452, AC2 — the re-entry half of the fix. The self-repair
force-removed the directory and left the row ACTIVE, so the control plane went
on believing a checkout existed at a path that no longer held one. Closing the
writer is the fix; this is the invariant that catches the state anyway.

Its re-entry policy is `EXPECTED` rather than `DEFECT`, and that is a decision
rather than a shrug: after the fix no writer in the tree can reach this state,
but an operator running `git worktree remove` by hand is a legitimate and
un-guardable way in. `DEFECT` would escalate every one of those to a bug at the
first ticket.
"""

from __future__ import annotations

from datetime import date
from pathlib import Path

import pytest
from loregarden.models.domain import (
    AgentRun,
    InvariantReentry,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    Worktree,
    WorktreeState,
)
from sqlmodel import Session
from tests.worktree_helpers import git, make_repo


@pytest.fixture(name="INVARIANT")
def invariant_fixture():
    """Resolved here rather than at import, so a module that has not registered
    it yet fails these tests and nothing else in the suite's collection."""
    from loregarden.services import invariant_registry

    return invariant_registry.ACTIVE_WORKTREE_ROWS_HAVE_THEIR_CHECKOUT


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    ws = Workspace(slug="proj", name="proj", repo_path=str(make_repo(tmp_path)))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _row(session, workspace, *, path: str, external_id: str) -> Worktree:
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title="Add the thing",
        branch=f"loregarden/{external_id}",
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    run = AgentRun(
        run_code=f"run-{external_id}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    row = Worktree(
        workspace_id=workspace.id,
        agent_run_id=run.id,
        ticket_id=ticket.id,
        parent_branch="main",
        worktree_path=path,
        branch=ticket.branch,
        state=WorktreeState.ACTIVE,
    )
    session.add(row)
    session.commit()
    session.refresh(row)
    return row


def _under_the_base(workspace, name: str) -> str:
    """A path where the control plane actually cuts checkouts.

    `WorktreeService.worktree_base` is ``repo.parent / ".worktrees"``, and a
    recorded path outside it is refused OUTSIDE_BASE as a permanent fault an
    operator must clear — deliberately, so an absent-but-out-of-base row is
    told apart from stale bookkeeping. The design built these fixtures in
    `tmp_path` directly, which made every repair assertion in this module
    unsatisfiable for a reason that has nothing to do with the invariant; it
    is the same correction the test-design stage made to `test_git_branch`'s
    `scratchpad` (lg-external-harness-1452, implement).
    """
    return str(Path(workspace.repo_path).parent / ".worktrees" / name)


@pytest.fixture(name="orphan")
def orphan_fixture(session, workspace, tmp_path) -> Worktree:
    """The state: the row says ACTIVE, and nothing is at the path it records."""
    return _row(
        session, workspace, path=_under_the_base(workspace, "gone"), external_id="lg-orphan"
    )


@pytest.fixture(name="live")
def live_fixture(session, workspace, tmp_path) -> Worktree:
    """A row whose checkout is right where it says — the one that must be left alone."""
    repo = Path(workspace.repo_path)
    tree = repo.parent / ".worktrees" / "live"
    tree.parent.mkdir(parents=True, exist_ok=True)
    git(repo, "worktree", "add", "-q", "-b", "loregarden/lg-live", str(tree))
    return _row(session, workspace, path=str(tree), external_id="lg-live")


def test_the_detector_lists_an_active_row_whose_checkout_is_gone(INVARIANT, session, orphan, live):
    violations = list(INVARIANT.detect(session))

    assert [v.subject_id for v in violations] == [orphan.id]
    assert orphan.worktree_path in violations[0].detail, (
        "the finding names the path, because that is what a person goes and looks at"
    )
    assert violations[0].ticket_id == orphan.ticket_id


def test_the_repair_settles_the_row_and_leaves_the_live_checkout_alone(
    INVARIANT, session, orphan, live
):
    """Settled through the existing `worktree_lifecycle` path, not a second one."""
    repaired = INVARIANT.repair(session, list(INVARIANT.detect(session)))

    assert repaired == 1
    session.refresh(orphan)
    session.refresh(live)
    assert orphan.state == WorktreeState.CLEANUP.value
    assert live.state == WorktreeState.ACTIVE.value
    assert Path(live.worktree_path).is_dir()


def test_one_pass_leaves_nothing_for_the_next(INVARIANT, session, orphan):
    INVARIANT.repair(session, list(INVARIANT.detect(session)))

    assert list(INVARIANT.detect(session)) == []


def test_a_cleanup_row_with_no_directory_is_not_a_violation(
    INVARIANT, session, workspace, tmp_path
):
    """Only ACTIVE rows claim a checkout. A settled row saying nothing is there
    is the row agreeing with the disk, which is the goal state, not the bug."""
    row = _row(
        session,
        workspace,
        path=_under_the_base(workspace, "already-gone"),
        external_id="lg-settled",
    )
    row.state = WorktreeState.CLEANUP
    session.add(row)
    session.commit()

    assert list(INVARIANT.detect(session)) == []


def test_the_invariant_is_registered_scheduled_and_honest_about_its_reentry(INVARIANT):
    """AC2. Registered in `invariant_registry` and scheduled in `INVARIANTS`."""
    from loregarden.services.invariant_registry import INVARIANTS

    assert INVARIANT in INVARIANTS
    assert INVARIANT.name == "active_worktree_rows_have_their_checkout"
    assert INVARIANT.ticket == "lg-external-harness-1452"
    assert INVARIANT.reentry is InvariantReentry.EXPECTED
    assert "by hand" in INVARIANT.expected_because, (
        "the legitimate way in is an operator removing a checkout outside the control plane"
    )
    assert isinstance(INVARIANT.since, date)


# ---------------------------------------------------------------------------
# lg-external-harness-1452, test-break. The detector's edges.
#
# The design proves the detector on one orphan beside one live row, which is
# the shape AC2 describes. Three things it does not pin: the count (a repair
# that settles the first row and returns `len(violations)` reads as correct at
# n=1), what "absent" means for a path that exists and is not a checkout, and
# what a row recording no path at all does. The last one is not hypothetical:
# `Path("").exists()` is False, so an empty `worktree_path` is a violation
# whose "go and look at it" names nothing.
# ---------------------------------------------------------------------------


@pytest.fixture(name="two_orphans")
def two_orphans_fixture(session, workspace, tmp_path) -> list[Worktree]:
    return [
        _row(
            session, workspace, path=_under_the_base(workspace, "gone-a"), external_id="lg-orphan-a"
        ),
        _row(
            session, workspace, path=_under_the_base(workspace, "gone-b"), external_id="lg-orphan-b"
        ),
    ]


def test_the_repair_counts_every_row_it_settled(INVARIANT, session, two_orphans, live):
    """n>1, because n=1 cannot tell a count from a constant.

    `return 1`, `return len(violations)` and a loop that settles the first row
    and breaks all agree at one violation. The reconcile pass reports this
    number to the operator, so a count that is not the number of rows settled
    is a silent failure wearing a receipt.
    """
    repaired = INVARIANT.repair(session, list(INVARIANT.detect(session)))

    assert repaired == 2
    for orphan in two_orphans:
        session.refresh(orphan)
        assert orphan.state == WorktreeState.CLEANUP.value
    session.refresh(live)
    assert live.state == WorktreeState.ACTIVE.value


def test_the_detector_answers_the_same_twice(INVARIANT, session, two_orphans, live):
    """Determinism, and order-independence of the answer.

    Two passes of the reconcile timer over an unchanged database must list the
    same rows. A detector reading `.first()`, or iterating a set, can pick a
    different subject each pass — which turns one state into a stream of
    findings an operator cannot tell from several.
    """
    first = sorted(v.subject_id for v in INVARIANT.detect(session))
    second = sorted(v.subject_id for v in INVARIANT.detect(session))

    assert first == second
    assert first == sorted(row.id for row in two_orphans)


def test_a_recorded_path_that_is_a_file_is_not_a_checkout(INVARIANT, session, workspace, tmp_path):
    """ "Absent" is about a checkout, not about `os.path.exists`.

    A file where a checkout should be is the state after something wrote over
    the path, and it is as un-usable as an empty one — but an `exists()` test
    calls it healthy and leaves the row ACTIVE forever, which is the stale
    state this invariant exists to clear.
    """
    stump = Path(_under_the_base(workspace, "was-a-worktree"))
    stump.parent.mkdir(parents=True, exist_ok=True)
    stump.write_text("not a checkout\n", encoding="utf-8")
    row = _row(session, workspace, path=str(stump), external_id="lg-stump")

    assert [v.subject_id for v in INVARIANT.detect(session)] == [row.id]


def test_a_row_recording_no_path_at_all_is_a_violation_that_names_itself(
    INVARIANT, session, workspace
):
    """The empty string, which `Path("").exists()` already calls False.

    So it is already detected; the question is whether the finding is
    readable. "go and look at ''" is not something a person can act on, so the
    finding has to name the row or its ticket instead of only the path.
    """
    row = _row(session, workspace, path="", external_id="lg-pathless")

    violations = list(INVARIANT.detect(session))

    assert [v.subject_id for v in violations] == [row.id]
    assert row.id in violations[0].detail or row.ticket_id in violations[0].detail, (
        "a finding about a row with no path has to name the row"
    )
    assert INVARIANT.repair(session, violations) == 1
    assert list(INVARIANT.detect(session)) == []


def test_two_active_rows_at_one_path_are_both_settled(INVARIANT, session, workspace, tmp_path):
    """Duplicates, because nothing in the schema forbids them.

    A detector that groups by path, or a repair that settles "the row for this
    path", leaves the second one ACTIVE — and the next pass reports it again,
    forever, which is the sweep-that-nobody-reads failure the invariant
    registry replaced.
    """
    gone = _under_the_base(workspace, "gone-twice")
    rows = [
        _row(session, workspace, path=gone, external_id="lg-dup-a"),
        _row(session, workspace, path=gone, external_id="lg-dup-b"),
    ]

    assert INVARIANT.repair(session, list(INVARIANT.detect(session))) == 2
    for row in rows:
        session.refresh(row)
        assert row.state == WorktreeState.CLEANUP.value
    assert list(INVARIANT.detect(session)) == []


def test_the_repair_of_nothing_settles_nothing(INVARIANT, session, live):
    """The empty input, which the reconcile pass hands it on every healthy tick."""
    assert INVARIANT.repair(session, []) == 0
    session.refresh(live)
    assert live.state == WorktreeState.ACTIVE.value
