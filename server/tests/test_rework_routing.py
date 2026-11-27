"""A hand-off between implementers is not a rejection; a reject goes to the files' owner.

Live database, Oct 20 - Nov 18: 23 implement rounds across 18 tickets were one
implementer naming its sibling for the other half. Each was charged to the
rework cap like a reviewer's reject, so a full-stack ticket could block on one
real defect. And a review reject citing `server/` files went back through
keyword routing to the frontend implementer, which only handed it on.
"""

from __future__ import annotations

import json
import subprocess
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    ClassifyRoute,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    Workspace,
)
from loregarden.services.invariant_registry import INVARIANTS
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.rework_cap_checks import (
    REWORK_PAUSES_STAND_ON_CHARGED_ROUNDS,
    pauses_on_uncharged_rounds,
)
from loregarden.services.rework_feedback import (
    MAX_REWORK_REROUTES,
    record_rework_feedback,
    rework_reroute_count,
)
from loregarden.services.rework_pause import ReworkPausePayload
from loregarden.services.rework_routing import handoff_target, owner_of_finding
from loregarden.services.workflow_state import initial_stages_json
from loregarden.testing.factories import findings_fragment
from sqlmodel import Session

FRONTEND = "frontend_implementer"
BACKEND = "backend_implementer"

IMPLEMENT = WorkflowStageDef(
    key="implement",
    name="implement",
    order=2,
    stage_type="classify",
    classify_routes=[
        ClassifyRoute(agent_id=FRONTEND, specialties=["client"], default=True),
        ClassifyRoute(agent_id=BACKEND, specialties=["server"]),
    ],
)
REVIEW = WorkflowStageDef(key="review", name="review", order=3, agent_id="architecture_reviewer")
STAGES = [
    WorkflowStageDef(key="plan", name="plan", order=1, agent_id="planner"),
    IMPLEMENT,
    REVIEW,
    WorkflowStageDef(key="done", name="done", order=4, terminal=True),
]


def _git(root, *args: str) -> None:
    subprocess.run(["git", *args], cwd=root, check=True, capture_output=True)


def _commit(root, name: str) -> None:
    (root / name).write_text(name)
    _git(root, "add", "-A")
    _git(root, "commit", "-qm", name)


@pytest.fixture
def ticket(db_session: Session, git_repo) -> Ticket:
    template = WorkflowTemplate(
        slug=f"rr-{uuid4().hex[:8]}",
        name="Rework routing",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json=json.dumps(
            [
                {"from": "plan", "to": "implement", "when": "pass"},
                {"from": "implement", "to": "review", "when": "pass"},
                {"from": "review", "to": "done", "when": "pass"},
                {"from": "review", "to": "implement", "when": "reject"},
            ]
        ),
    )
    db_session.add(template)
    db_session.commit()
    workspace = Workspace(
        slug=f"rr-{uuid4().hex[:8]}",
        name="Rework routing",
        repo_path=str(git_repo),
        workflow_template_id=template.id,
    )
    db_session.add(workspace)
    db_session.commit()
    row = Ticket(
        external_id=f"rr-{uuid4().hex[:8]}",
        workspace_id=workspace.id,
        title="Full-stack ticket",
        state=TicketState.IN_PROGRESS,
        workflow_stage_key="implement",
        workflow_stage_status=StageStatus.RUNNING,
    )
    db_session.add(row)
    db_session.commit()
    db_session.refresh(row)
    db_session.add(
        WorkflowInstance(
            ticket_id=row.id,
            template_id=template.id,
            current_stage_key="implement",
            stages_json=initial_stages_json(STAGES),
        )
    )
    db_session.commit()
    return row


def _report(*, reroute_to_agent: str = "", context: str = "rest is not mine") -> str:
    payload = {
        "status": "needs_rework",
        "confidence": 0.9,
        "reroute_context": context,
    } | ({"reroute_to_agent": reroute_to_agent} if reroute_to_agent else {})
    body = json.dumps(payload)[:-1] + findings_fragment("needs_rework") + "}"
    return f"Narrative.\n<<<LOREGARDEN_STAGE_REPORT>>>\n{body}\n<<<END_STAGE_REPORT>>>\n"


def _complete(session: Session, ticket: Ticket, *, agent_id: str, stage_key: str, stdout: str):
    orch = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        current_stage_key=stage_key,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(orch)
    session.commit()
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:8]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=RunStatus.RUNNING,
        orchestration_run_id=orch.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    OrchestrationService(session).complete_run(run, status=RunStatus.SUCCEEDED, stdout=stdout)
    session.refresh(ticket)


def _charge_to_cap(session: Session, ticket: Ticket) -> None:
    for i in range(MAX_REWORK_REROUTES):
        record_rework_feedback(
            session, ticket, target_stage="implement", from_stage="review", context=f"defect {i}"
        )


def test_a_sibling_handoff_is_not_charged_to_the_rework_cap(db_session, ticket):
    _charge_to_cap(db_session, ticket)

    _complete(
        db_session,
        ticket,
        agent_id=FRONTEND,
        stage_key="implement",
        stdout=_report(reroute_to_agent=BACKEND),
    )

    assert ticket.state is not TicketState.BLOCKED
    assert ticket.workflow_stage_key == "implement"
    assert ticket.scope_reroute_agent == BACKEND
    assert rework_reroute_count(db_session, ticket, "implement") == MAX_REWORK_REROUTES


def test_a_reject_at_the_cap_still_blocks(db_session, ticket):
    _charge_to_cap(db_session, ticket)
    ticket.workflow_stage_key = "review"
    db_session.add(ticket)
    db_session.commit()

    _complete(
        db_session, ticket, agent_id="architecture_reviewer", stage_key="review", stdout=_report()
    )

    assert ticket.state is TicketState.BLOCKED


def test_a_handoff_that_moved_nothing_is_charged(db_session, ticket):
    for agent in (BACKEND, FRONTEND):
        record_rework_feedback(
            db_session,
            ticket,
            target_stage="implement",
            from_stage="implement",
            context=f"over to {agent}",
            handoff_to=agent,
        )

    assert rework_reroute_count(db_session, ticket, "implement") == 1


def test_a_handoff_after_new_work_is_free_again(db_session, ticket, git_repo):
    record_rework_feedback(
        db_session,
        ticket,
        target_stage="implement",
        from_stage="implement",
        context="client half done",
        handoff_to=BACKEND,
    )
    _commit(git_repo, "server-half")
    record_rework_feedback(
        db_session,
        ticket,
        target_stage="implement",
        from_stage="implement",
        context="server half done, client copy left",
        handoff_to=FRONTEND,
    )

    assert rework_reroute_count(db_session, ticket, "implement") == 0


def test_a_review_reject_citing_server_files_goes_to_the_backend(db_session, ticket):
    ticket.workflow_stage_key = "review"
    db_session.add(ticket)
    db_session.commit()

    _complete(
        db_session,
        ticket,
        agent_id="architecture_reviewer",
        stage_key="review",
        stdout=_report(context="server/loregarden/services/x.py:12 drops the error"),
    )

    assert ticket.workflow_stage_key == "implement"
    assert ticket.scope_reroute_agent == BACKEND


@pytest.mark.parametrize(
    ("context", "owner"),
    [
        ("server/loregarden/a.py:3 and server/tests/test_a.py", BACKEND),
        ("client/src/components/X.tsx:40 loses focus", FRONTEND),
        ("server/loregarden/a.py and client/src/X.tsx disagree", ""),
        ("services/a.py is wrong", ""),
        (".lefthook/scripts/g.py and server/loregarden/a.py", BACKEND),
        ("no path at all", ""),
    ],
)
def test_owner_of_finding(context, owner):
    assert owner_of_finding(IMPLEMENT, context) == owner


def test_a_judging_stage_never_hands_off():
    assert (
        handoff_target(
            REVIEW,
            agent_id="architecture_reviewer",
            target_stage="review",
            reroute_to_agent=BACKEND,
        )
        == ""
    )


def test_a_pause_on_uncharged_rounds_is_reported(db_session, ticket):
    record_rework_feedback(
        db_session,
        ticket,
        target_stage="implement",
        from_stage="implement",
        context="over to backend",
        handoff_to=BACKEND,
    )
    db_session.add(
        Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.REWORK_PAUSE,
            title="Rework paused",
            level="high",
            stage_key="implement",
            tool_input_json=ReworkPausePayload(target_stage="implement").model_dump_json(),
            status=ApprovalStatus.PENDING,
        )
    )
    db_session.commit()

    found = pauses_on_uncharged_rounds(db_session)

    assert REWORK_PAUSES_STAND_ON_CHARGED_ROUNDS in INVARIANTS
    assert [v.ticket_id for v in found] == [ticket.id]
    _charge_to_cap(db_session, ticket)
    record_rework_feedback(
        db_session, ticket, target_stage="implement", from_stage="review", context="one more"
    )
    assert pauses_on_uncharged_rounds(db_session) == []


def test_a_pause_with_no_readable_target_or_no_ticket_is_not_judged(db_session, ticket):
    """There is nothing to count rounds against, so it is no evidence of a broken cap."""
    db_session.add_all(
        [
            Approval(
                ticket_id=None,
                workspace_id=ticket.workspace_id,
                kind=ApprovalKind.REWORK_PAUSE,
                title="Rework paused",
                level="high",
                tool_input_json=ReworkPausePayload(target_stage="implement").model_dump_json(),
                status=ApprovalStatus.PENDING,
            ),
            Approval(
                ticket_id=ticket.id,
                workspace_id=ticket.workspace_id,
                kind=ApprovalKind.REWORK_PAUSE,
                title="Rework paused",
                level="high",
                tool_input_json="not a payload",
                status=ApprovalStatus.PENDING,
            ),
        ]
    )
    db_session.commit()

    assert pauses_on_uncharged_rounds(db_session) == []
