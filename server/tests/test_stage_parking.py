"""Resolving a parked stage.

Parking is covered in `test_handoff_boundary`; everything here starts *after*
the approval exists, which is the half that had no tests at all and where the
defect lived. A park was raised as a WORKFLOW_GATE, so approving it ran the gate
resolution and marked the stage DONE without ever running it — the operator's
click to unstick a broken checkout silently dropped a stage of work, and an
`auto_approve` run did the same to itself with nobody watching.
"""

from unittest import mock

import pytest
from loregarden.models.domain import (
    AgentRun,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.doctor import park_for_environment
from loregarden.services.orchestration import (
    ApprovalService,
    OrchestrationService,
    _consume_dispatch_waiver,
)
from loregarden.services.subtree_auto_run import auto_resolve_awaiting_gate
from loregarden.services.workflow_state import reconcile_workflow_state
from sqlmodel import Session, select
from tests.factories import make_orchestration_run

PARK_SUMMARY = "git_core_bare: core.bare=true in /tmp/repo, which has a working tree."


@pytest.fixture(name="parked")
def parked_fixture(db_session: Session):
    """A ticket on a real template with its first stage parked on an approval.

    Built in a fixture, not the test body: a broken setup here would otherwise
    read as a behaviour failure in whichever assertion tripped first.
    """
    ticket = db_session.exec(select(Ticket)).first()
    orch = OrchestrationService(db_session)
    orch.ensure_workflow_instance(ticket)
    instance, stages = orch._resolve_stages(ticket)
    assert instance and stages
    stage_key = stages[0].key
    ticket.workflow_stage_key = stage_key
    db_session.add(ticket)
    db_session.commit()

    run = AgentRun(
        run_code="r-park",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key=stage_key,
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    approval = park_for_environment(db_session, run=run, ticket=ticket, summary=PARK_SUMMARY)
    db_session.refresh(ticket)
    return ticket, run, approval, stage_key


def test_a_park_is_not_a_workflow_gate(parked):
    """The whole fix hangs off this. A gate says "the work is good"; a park says
    "the machine is broken" — routed through one resolution they cannot both be
    right, and it was the park that lost."""
    _, _, approval, _ = parked
    assert approval.kind is ApprovalKind.STAGE_PARK


def test_approving_a_park_reruns_the_stage_rather_than_completing_it(db_session, parked):
    """The defect, inverted. This asserted DONE before the fix — a stage the
    agent never ran, recorded as finished, with the workflow moved on past it."""
    ticket, _, approval, stage_key = parked
    assert ticket.workflow_stage_status == StageStatus.AWAITING

    with mock.patch("loregarden.services.orchestration.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(approval.id, approved=True)

    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.PENDING
    assert ticket.workflow_stage_key == stage_key
    assert scheduled.called


def test_approving_arms_the_waiver_the_next_dispatch_spends(db_session, parked):
    """Without it the stage re-dispatches, meets the same failing check, and
    parks again — an approve button that cannot ever get past itself."""
    ticket, _, approval, stage_key = parked

    with mock.patch("loregarden.services.orchestration.schedule_orchestration"):
        ApprovalService(db_session).resolve(approval.id, approved=True)

    db_session.refresh(ticket)
    assert ticket.dispatch_waiver_stage_key == stage_key
    assert ticket.dispatch_waiver_approval_id == approval.id


def test_rejecting_a_park_blocks_in_place_and_does_not_reroute(db_session, parked):
    """Reject means "fix the machine first". The gate path would have sent this
    to an upstream *work* stage, spending a rework round re-planning a ticket
    whose plan was never the problem."""
    ticket, _, approval, stage_key = parked

    with mock.patch("loregarden.services.orchestration.schedule_orchestration") as scheduled:
        ApprovalService(db_session).resolve(approval.id, approved=False)

    db_session.refresh(ticket)
    assert ticket.workflow_stage_status == StageStatus.BLOCKED
    assert ticket.workflow_stage_key == stage_key
    assert "core.bare" in ticket.blocking_issues
    assert not ticket.dispatch_waiver_stage_key
    assert not scheduled.called


def test_a_park_cannot_be_rerouted_to_another_stage(db_session, parked):
    """`route_to_stage_key` is a gate affordance — "approve, but formalize this
    first". There is no such answer to a broken checkout."""
    _, _, approval, _ = parked

    with pytest.raises(ValueError, match="workflow-gate"):
        ApprovalService(db_session).resolve(approval.id, approved=True, route_to_stage_key="triage")


def test_autopilot_pauses_on_a_park_instead_of_approving_its_own(db_session, parked):
    """The unattended half of the defect: `auto_resolve_awaiting_gate` selected
    the park, auto-approved it, and the run continued past a stage nobody ran.
    No amount of auto_approve makes a broken box not broken."""
    ticket, _, approval, stage_key = parked
    orch_run = make_orchestration_run(
        db_session, workspace_id=ticket.workspace_id, ticket_id=ticket.id
    )

    assert auto_resolve_awaiting_gate(db_session, ticket, orch_run, stage_key) is False

    db_session.refresh(ticket)
    db_session.refresh(approval)
    assert ticket.workflow_stage_status == StageStatus.AWAITING
    assert approval.status is ApprovalStatus.PENDING


def test_the_waiver_is_spent_by_the_stage_it_names_and_only_once(db_session, parked):
    ticket, _, approval, stage_key = parked
    ticket.dispatch_waiver_stage_key = stage_key
    ticket.dispatch_waiver_approval_id = approval.id

    assert _consume_dispatch_waiver(ticket, stage_key) == approval.id
    assert ticket.dispatch_waiver_stage_key == ""
    assert _consume_dispatch_waiver(ticket, stage_key) == ""


def test_another_stage_does_not_spend_a_waiver_meant_for_this_one(db_session, parked):
    """A waiver the dispatch did not match is a request nobody satisfied, so it
    stands — and a person waiving `implement` has not waived `verify`."""
    ticket, _, approval, stage_key = parked
    ticket.dispatch_waiver_stage_key = stage_key
    ticket.dispatch_waiver_approval_id = approval.id

    assert _consume_dispatch_waiver(ticket, "some-other-stage") == ""
    assert ticket.dispatch_waiver_stage_key == stage_key


def test_the_waiver_survives_a_workflow_reconcile(db_session, parked):
    """`ticket.next_agent` was silently restored by the read path, which undid
    its clear before the pinned dispatch ever happened. Same shape of pin, so
    the same trap is worth pinning shut."""
    ticket, _, approval, stage_key = parked
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    ticket.dispatch_waiver_stage_key = stage_key
    ticket.dispatch_waiver_approval_id = approval.id

    reconcile_workflow_state(ticket, instance, stages, persist=False)

    assert ticket.dispatch_waiver_stage_key == stage_key
    assert ticket.dispatch_waiver_approval_id == approval.id
