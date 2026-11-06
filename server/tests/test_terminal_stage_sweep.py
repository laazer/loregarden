"""A ticket parked on its terminal stage is finished by the sweep (801).

The 754 shape: the landing conflicted, the terminal stage blocked, the
orchestration finished BLOCKED. Someone resolved the conflict and cleared the
block, which returns the terminal stage to PENDING — a state only an
orchestration loop consumes, and there is no loop left. Nothing re-entered,
and the ticket read `in_progress` with no blocking text for three hours.
"""

from __future__ import annotations

import json
from datetime import timedelta

import pytest
from loregarden.models.domain import (
    AgentRun,
    Artifact,
    ArtifactKind,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.services.gate_runner import GATE_EX_UNAVAILABLE
from loregarden.services.invariant_registry import REFUSED_LANDINGS_WAIT_FOR_A_CHANGE
from loregarden.services.land_ticket import land_ticket
from loregarden.services.landing_refused import UNAVAILABLE_RETRY_AFTER
from loregarden.services.reconciliation import reconcile_once
from loregarden.services.target_branch import resolve_target_branch
from loregarden.services.terminal_stage_sweep import (
    finish_parked_terminal_tickets,
    finish_tickets_whose_landing_cleared,
    tickets_whose_landing_cleared,
)
from sqlmodel import Session, col, select
from tests.land_receipt_helpers import FAILING_SUITE, configure
from tests.worktree_helpers import at_terminal_stage, blocking_text, commit_on, git, make_repo


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="repo")
def repo_fixture(tmp_path):
    return make_repo(tmp_path)


@pytest.fixture(name="workspace")
def workspace_fixture(session, repo):
    ws = Workspace(slug="parked", name="parked", repo_path=str(repo))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="milestone")
def milestone_fixture(session, workspace):
    ms = Ticket(
        external_id="lg-ms-801",
        workspace_id=workspace.id,
        title="Milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    session.add(ms)
    session.commit()
    session.refresh(ms)
    return ms


@pytest.fixture(name="parked")
def parked_fixture(session, workspace, repo, milestone):
    """A ticket with work on its branch, parked on its terminal stage.

    Built in a fixture, not a test body: a repro whose setup breaks must be an
    ERROR, not a passing assertion about nothing.
    """
    ticket = Ticket(
        external_id="lg-parked-1",
        workspace_id=workspace.id,
        title="Parked on done",
        branch="loregarden/lg-parked-1",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    target = resolve_target_branch(session, ticket, workspace, repo_root=repo)
    git(repo, "branch", ticket.branch, target)
    commit_on(repo, ticket.branch, "work.txt", "work\n")
    at_terminal_stage(session, ticket)
    return ticket


def test_boot_recovery_leaves_a_parked_ticket_for_the_timer(session, parked) -> None:
    """A landing runs the tree's full suite, and boot runs on the event loop before
    the server answers: three parked tickets kept a restarted server dark for
    over half an hour. The timer lands it a tick later, on a worker thread."""
    reconcile_once(session, at_startup=True)

    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS
    assert parked.workflow_stage_status == StageStatus.PENDING


def _target(session, ticket, workspace, repo):
    return resolve_target_branch(session, ticket, workspace, repo_root=repo)


def test_a_ticket_parked_on_its_terminal_stage_is_landed_and_finished(
    session, workspace, repo, parked
):
    """754, after the conflict was resolved: nothing was left to finish it."""
    assert parked.state == TicketState.IN_PROGRESS
    assert parked.workflow_stage_key == "done"
    assert parked.workflow_stage_status == StageStatus.PENDING

    finished = finish_parked_terminal_tickets(session)

    assert [t.id for t in finished] == [parked.id]
    session.refresh(parked)
    assert parked.state == TicketState.DONE
    assert parked.landed_sha, "the work reached its target"
    assert parked.landed_branch == _target(session, parked, workspace, repo)


def test_a_landing_that_still_conflicts_blocks_with_its_reason_and_is_not_retried(
    session, workspace, repo, parked
):
    """The sweep must not paper over a conflict, and must reach a fixpoint:
    a BLOCKED terminal stage is no longer PENDING, so the next sweep skips it."""
    target = _target(session, parked, workspace, repo)
    # The person-facing block is what this checks; the resolver is on by default.
    parked.git_automation_json = json.dumps({"auto_resolve_conflicts": False})
    session.add(parked)
    session.commit()
    commit_on(repo, parked.branch, "shared.txt", "ticket\n")
    commit_on(repo, target, "shared.txt", "sibling\n")

    finish_parked_terminal_tickets(session)

    session.refresh(parked)
    assert parked.state == TicketState.BLOCKED
    assert parked.workflow_stage_status == StageStatus.BLOCKED
    assert "shared.txt" in blocking_text(session, parked)
    assert parked.landed_sha == ""

    assert finish_parked_terminal_tickets(session) == [], "a blocked stage is not swept again"


def test_a_ticket_with_a_live_run_is_left_alone(session, parked):
    session.add(
        AgentRun(
            run_code="run-parked",
            ticket_id=parked.id,
            workspace_id=parked.workspace_id,
            agent_id="backend_implementer",
            stage_key="done",
            status=RunStatus.RUNNING,
        )
    )
    session.commit()

    assert finish_parked_terminal_tickets(session) == []
    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS


def test_a_ticket_with_a_live_orchestration_is_left_to_it(session, parked):
    session.add(
        OrchestrationRun(
            run_code="orch-parked",
            ticket_id=parked.id,
            workspace_id=parked.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    session.commit()

    assert finish_parked_terminal_tickets(session) == []
    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS


def test_a_hand_set_state_is_not_overwritten(session, parked):
    parked.state_locked = True
    session.add(parked)
    session.commit()

    assert finish_parked_terminal_tickets(session) == []
    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS


def test_a_ticket_still_short_of_its_terminal_stage_is_not_finished(session, parked):
    """The cursor, not the row's status, decides — a mid-workflow stage reads
    PENDING too, and finishing one would skip every stage after it."""
    parked.workflow_stage_key = "work"
    parked.workflow_stage_status = StageStatus.PENDING
    session.add(parked)
    session.commit()

    assert finish_parked_terminal_tickets(session) == []
    session.refresh(parked)
    assert parked.state == TicketState.IN_PROGRESS


@pytest.mark.filterwarnings("error::sqlalchemy.exc.SAWarning")
def test_the_cleared_landing_sweep_skips_a_blocked_initiative(session):
    """An initiative has no workspace, so nothing to land; the sweep must not look one up."""
    initiative = Ticket(
        external_id="lg-init-1",
        title="Initiative",
        work_item_type=WorkItemType.INITIATIVE,
        state=TicketState.BLOCKED,
    )
    session.add(initiative)
    session.commit()

    assert finish_tickets_whose_landing_cleared(session) == []


# --- a landing land verification refused (2284) -----------------------------

#: `sysexits.h` EX_UNAVAILABLE: the suite never ran (a timeout, a missing tool).
NEVER_RAN = f"python3 -c 'import sys; sys.exit({GATE_EX_UNAVAILABLE})'"


def _refused(session, parked, command: str) -> Ticket:
    """Land the parked ticket through the sweep with `command` as its suite, and
    check the suite refused it: blocked on its terminal stage, nothing landed."""
    configure(session, parked, land_verify_commands=[command])
    finish_parked_terminal_tickets(session)
    session.refresh(parked)
    assert parked.state == TicketState.BLOCKED, "the suite refused the landing"
    assert parked.landed_sha == ""
    return parked


def _receipts(session, ticket) -> list[Artifact]:
    return list(
        session.exec(
            select(Artifact)
            .where(Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.LAND_VERIFICATION)
            .order_by(col(Artifact.created_at).desc())
        ).all()
    )


def test_a_landing_its_tests_refused_is_not_retried_on_the_same_tips(session, parked):
    """The merge previews clean, so the sweep used to re-land it every pass,
    holding a heavy lease for a suite that refused the same tree last time."""
    _refused(session, parked, FAILING_SUITE)

    assert tickets_whose_landing_cleared(session) == []
    assert finish_tickets_whose_landing_cleared(session) == []
    assert len(_receipts(session, parked)) == 1, "the suite was not run again"


def test_a_new_commit_on_the_branch_earns_the_landing_another_attempt(session, repo, parked):
    _refused(session, parked, FAILING_SUITE)

    commit_on(repo, parked.branch, "fix.txt", "fixed\n")

    assert [t.id for t in tickets_whose_landing_cleared(session)] == [parked.id]


def test_a_new_commit_on_the_target_earns_the_landing_another_attempt(
    session, workspace, repo, parked
):
    _refused(session, parked, FAILING_SUITE)

    commit_on(repo, _target(session, parked, workspace, repo), "sibling.txt", "sibling\n")

    assert [t.id for t in tickets_whose_landing_cleared(session)] == [parked.id]


def test_tests_that_never_ran_are_retried_only_once_the_wait_is_over(session, parked):
    """A timeout is about the machine at the time, not the tree: it stands for a
    while, then the same tips get another try."""
    _refused(session, parked, NEVER_RAN)
    assert tickets_whose_landing_cleared(session) == [], "inside the wait"

    (receipt,) = _receipts(session, parked)
    receipt.created_at = receipt.created_at - UNAVAILABLE_RETRY_AFTER - timedelta(minutes=1)
    session.add(receipt)
    session.commit()

    assert [t.id for t in tickets_whose_landing_cleared(session)] == [parked.id]


def test_a_landing_verified_again_on_refused_tips_is_reported(session, workspace, parked):
    """The re-entry detector: whatever writer retries a refused landing on
    unchanged tips (here `land_ticket` called directly), it is reported."""
    _refused(session, parked, FAILING_SUITE)
    assert REFUSED_LANDINGS_WAIT_FOR_A_CHANGE.detect(session) == []

    land_ticket(session, parked, workspace)

    (violation,) = REFUSED_LANDINGS_WAIT_FOR_A_CHANGE.detect(session)
    assert violation.ticket_id == parked.id
    assert "failed refusal at the same tips" in violation.detail


def test_a_retry_after_a_new_commit_is_not_reported(session, workspace, repo, parked):
    _refused(session, parked, FAILING_SUITE)
    commit_on(repo, parked.branch, "fix.txt", "still broken\n")

    land_ticket(session, parked, workspace)

    assert len(_receipts(session, parked)) == 2
    assert REFUSED_LANDINGS_WAIT_FOR_A_CHANGE.detect(session) == []
