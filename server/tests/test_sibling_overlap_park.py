"""Parking a stage that is about to write files an in-flight sibling claims.

Run anyway (approve) arms the waiver like any park; Wait (reject) links the
ticket to wait for the sibling instead of blocking it.
"""

from __future__ import annotations

import json
from pathlib import Path
from unittest import mock

import pytest
from loregarden.agents.executors.pre_dispatch_checks import park_unless_fit_to_run
from loregarden.agents.plan_context import ARTIFACT_KIND as PLAN_KIND
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketDependency,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.path_claim_enums import StageParkReason
from loregarden.services.approval_resolution import apply_park_resolution
from loregarden.services.orchestration import ApprovalService, OrchestrationService
from loregarden.services.preflight_ledger import PreflightLedger
from loregarden.services.sibling_overlap_park import (
    overlaps_to_park_on,
    park_for_sibling_overlap,
)
from loregarden.services.sibling_overlap_resolution import SiblingOverlapPark
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.worktree_snapshot import read_tree
from loregarden.testing.factories import make_artifact, make_ticket
from sqlmodel import Session, select
from tests.worktree_helpers import make_repo

LAND = "server/loregarden/services/land_ticket.py"


@pytest.fixture(name="pair")
def pair_fixture(db_session: Session):
    """The seeded ticket and a sibling under one milestone, both planning LAND.

    The sibling is in progress (in flight); the ticket has a plan and no
    changed paths, so it is about to start writing.
    """
    ticket = db_session.exec(select(Ticket)).first()
    milestone = make_ticket(
        db_session,
        workspace_id=ticket.workspace_id,
        external_id="ms-overlap",
        work_item_type=WorkItemType.MILESTONE,
    )
    ticket.parent_ticket_id = milestone.id
    ticket.state = TicketState.IN_PROGRESS
    db_session.add(ticket)
    db_session.commit()
    sibling = make_ticket(
        db_session,
        workspace_id=ticket.workspace_id,
        external_id="sib-overlap",
        parent_ticket_id=milestone.id,
        state=TicketState.IN_PROGRESS,
    )
    _plan(db_session, ticket, [LAND])
    _plan(db_session, sibling, [f"{LAND} — the landing path", "server/other.py"])
    return ticket, sibling


@pytest.fixture(name="parked")
def parked_fixture(db_session: Session, pair):
    """The ticket's first stage parked on its overlap with the sibling."""
    ticket, sibling = pair
    orch = OrchestrationService(db_session)
    orch.ensure_workflow_instance(ticket)
    _instance, stages = orch._resolve_stages(ticket)
    assert stages
    ticket.workflow_stage_key = stages[0].key
    db_session.add(ticket)
    db_session.commit()
    run = _run(db_session, ticket, stages[0].key)
    approval = park_for_sibling_overlap(
        db_session, run=run, ticket=ticket, overlaps=overlaps_to_park_on(db_session, ticket)
    )
    db_session.refresh(ticket)
    return ticket, sibling, approval


def _plan(session: Session, ticket: Ticket, files: list[str]) -> None:
    make_artifact(
        session,
        ticket_id=ticket.id,
        kind=PLAN_KIND,
        title="Plan",
        content={"approach": "x", "modules_touched": files},
    )


def _run(session: Session, ticket: Ticket, stage_key: str, *, waiver: str = "") -> AgentRun:
    run = AgentRun(
        run_code=f"r-{stage_key}-{waiver or 'none'}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key=stage_key,
        status=RunStatus.RUNNING,
        dispatch_waiver_approval_id=waiver,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _changed(session: Session, ticket: Ticket, *paths: str) -> None:
    session.add(
        AgentRun(
            run_code=f"changed-{ticket.external_id}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="backend_implementer",
            stage_key="implement",
            changed_paths_json=json.dumps(list(paths)),
            changed_paths_recorded_at=utcnow(),
        )
    )
    session.commit()


def _edges(session: Session, ticket: Ticket) -> set[str]:
    return set(
        session.exec(
            select(TicketDependency.depends_on_ticket_id).where(
                TicketDependency.ticket_id == ticket.id
            )
        ).all()
    )


# -- when to ask ------------------------------------------------------------


def test_a_ticket_about_to_write_beside_an_in_flight_sibling_is_asked(db_session, pair):
    ticket, sibling = pair

    [overlap] = overlaps_to_park_on(db_session, ticket)

    assert overlap.sibling.id == sibling.id
    assert [c.path for c in overlap.shared] == [LAND]


def test_a_sibling_still_in_the_backlog_is_not_asked_about(db_session, pair):
    """It will be asked when it starts, since this ticket is then in flight."""
    ticket, sibling = pair
    sibling.state = TicketState.BACKLOG
    db_session.add(sibling)
    db_session.commit()

    assert overlaps_to_park_on(db_session, ticket) == []


def test_a_ticket_that_has_already_written_is_not_asked(db_session, pair):
    """Ordering helps only before the first write; after it, the prompt warns."""
    ticket, _ = pair
    _changed(db_session, ticket, "server/elsewhere.py")

    assert overlaps_to_park_on(db_session, ticket) == []


@pytest.mark.parametrize("direction", ["ticket_waits", "sibling_waits"])
def test_tickets_already_ordered_either_way_are_not_asked(db_session, pair, direction):
    ticket, sibling = pair
    later, earlier = (ticket, sibling) if direction == "ticket_waits" else (sibling, ticket)
    TicketDependencyService(db_session).add_dependency(later.id, earlier.id)

    assert overlaps_to_park_on(db_session, ticket) == []


def test_a_sibling_answered_run_anyway_is_not_asked_about_again(db_session, parked):
    ticket, _, approval = parked
    with mock.patch("loregarden.services.orchestration.schedule_orchestration"):
        ApprovalService(db_session).resolve(approval.id, approved=True)

    assert overlaps_to_park_on(db_session, ticket) == []


# -- the park ---------------------------------------------------------------


def test_the_park_says_why_and_whom_rejecting_would_wait_for(parked):
    ticket, sibling, approval = parked

    assert approval.kind is ApprovalKind.STAGE_PARK
    assert approval.tool_name == StageParkReason.SIBLING_OVERLAP
    payload = SiblingOverlapPark.model_validate_json(approval.tool_input_json)
    assert payload.sibling_ids == [sibling.id]
    assert payload.sibling_external_ids == [sibling.external_id]
    assert LAND in approval.impact
    assert ticket.workflow_stage_status == StageStatus.AWAITING


# -- the answer -------------------------------------------------------------


def test_wait_links_the_ticket_to_the_sibling_and_requeues_the_stage(db_session, parked):
    ticket, sibling, approval = parked

    with mock.patch("loregarden.services.orchestration.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(approval.id, approved=False)

    db_session.refresh(ticket)
    assert _edges(db_session, ticket) == {sibling.id}
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert ticket.blocking_issues == ""
    # Resumed, so the dependency hold records the wait rather than nothing doing so.
    assert scheduled.called


def test_run_anyway_arms_the_waiver_and_links_nothing(db_session, parked):
    ticket, _, approval = parked

    with mock.patch("loregarden.services.orchestration.schedule_orchestration"):
        ApprovalService(db_session).resolve(approval.id, approved=True)

    db_session.refresh(ticket)
    assert ticket.dispatch_waiver_approval_id == approval.id
    assert _edges(db_session, ticket) == set()


def test_ignoring_the_card_orders_nothing(db_session, parked):
    """Ignore dismisses a stale card; linking edges for it would order work nobody chose."""
    ticket, _, approval = parked
    approval.status = ApprovalStatus.IGNORED
    db_session.add(approval)
    db_session.commit()
    orch = OrchestrationService(db_session)

    resumed = apply_park_resolution(
        db_session, orch, ticket, approval, approved=False, response_text="stale"
    )

    db_session.refresh(ticket)
    assert not resumed
    assert _edges(db_session, ticket) == set()
    assert ticket.workflow_stage_status == StageStatus.BLOCKED


def test_wait_that_would_close_a_loop_blocks_with_the_reason(db_session, parked):
    ticket, sibling, approval = parked
    TicketDependencyService(db_session).add_dependency(sibling.id, ticket.id)

    with mock.patch("loregarden.services.orchestration.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(approval.id, approved=False)

    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.BLOCKED
    assert "already waits for" in ticket.blocking_issues
    assert _edges(db_session, ticket) == set()
    assert not scheduled.called


# -- at dispatch ------------------------------------------------------------


@pytest.fixture(name="dispatch")
def dispatch_fixture(db_session: Session, pair, tmp_path):
    ticket, _sibling = pair
    repo: Path = make_repo(tmp_path)
    workspace = db_session.get(Workspace, ticket.workspace_id)
    workspace.repo_path = str(repo)
    db_session.add(workspace)
    db_session.commit()
    stage_key = ticket.workflow_stage_key or "implement"

    def check(*, waiver: str = "") -> AgentRun | None:
        run = _run(db_session, ticket, stage_key, waiver=waiver)
        return park_unless_fit_to_run(
            db_session,
            OrchestrationService(db_session),
            run,
            ticket,
            workspace,
            repo_root=repo,
            before=read_tree(repo),
            ledger=PreflightLedger(),
        )

    return ticket, check


def test_dispatch_parks_and_cancels_the_run_on_an_overlap(db_session, dispatch):
    ticket, check = dispatch

    parked = check()

    assert parked is not None and parked.status is RunStatus.CANCELLED
    approval = db_session.exec(select(Approval).where(Approval.ticket_id == ticket.id)).all()
    assert [a.tool_name for a in approval if a.kind is ApprovalKind.STAGE_PARK] == [
        StageParkReason.SIBLING_OVERLAP
    ]


def test_a_waived_dispatch_is_not_parked_on_an_overlap(dispatch):
    _, check = dispatch

    assert check(waiver="appr-run-anyway") is None


def test_a_failing_overlap_check_lets_the_stage_run_and_logs(dispatch, caplog):
    _, check = dispatch

    with mock.patch(
        "loregarden.agents.executors.pre_dispatch_checks.overlaps_to_park_on",
        side_effect=RuntimeError("boom"),
    ):
        assert check() is None

    # Logged with its traceback, so the skipped check is visible.
    assert any(r.levelname == "ERROR" and r.exc_info for r in caplog.records)
