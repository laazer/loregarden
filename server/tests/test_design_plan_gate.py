"""The orchestrator signs off a design plan by default; a person can take it back.

Before 746 a plan flowed from `plan-synthesis` / `ui-design` straight into
`implement`: no live template gated a design stage. Migration 0133 makes the
review point exist; the run's `approve_design_plans` dial (on by default,
off from the run modal) decides whether the run or a person sits at it.
`auto_approve` still signs off every gate, as it always has.
"""

from __future__ import annotations

import json
from uuid import uuid4

import pytest
from loregarden.db.migrations_design_plan_gate import (
    DESIGN_PLAN_STAGES,
    m_design_plan_gates,
)
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    ParallelAgentSpec,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.design_plan_gate import is_design_plan_stage, orchestrator_may_sign_off
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.subtree_auto_run import resolve_gate_if_permitted
from loregarden.services.workflow_state import initial_stages_json
from sqlalchemy import text
from sqlmodel import Session, select
from tests.history_helpers import decision_kinds

DESIGN = WorkflowStageDef(
    key="ui-design", name="UI design", order=1, agent_id="ui-design-decision", gate_required=True
)
PLAN = WorkflowStageDef(
    key="plan",
    name="Plan",
    order=2,
    stage_type="parallel",
    parallel_agents=[ParallelAgentSpec(agent_id="planner", skill_name="plan-simplest")],
)
IMPLEMENT = WorkflowStageDef(
    key="implement", name="Implement", order=3, agent_id="backend_implementer", gate_required=True
)
STAGES = [
    DESIGN,
    PLAN,
    IMPLEMENT,
    WorkflowStageDef(key="done", name="Done", order=4, terminal=True),
]


def _run(approve_design_plans: bool, *, auto_approve: bool = False) -> OrchestrationRun:
    return OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id="t",
        workspace_id="w",
        approve_design_plans=approve_design_plans,
        auto_approve=auto_approve,
        status=OrchestrationRunStatus.RUNNING,
    )


def test_which_stages_are_design_plans():
    assert is_design_plan_stage(DESIGN)
    assert is_design_plan_stage(PLAN)  # the planner as a parallel member
    assert not is_design_plan_stage(IMPLEMENT)


@pytest.mark.parametrize(
    ("stage", "flag", "auto", "expected"),
    [
        (DESIGN, True, False, True),
        (DESIGN, False, False, False),
        (IMPLEMENT, True, False, False),
        (IMPLEMENT, False, True, True),  # auto_approve still signs off everything
    ],
)
def test_who_may_sign_off(stage, flag, auto, expected):
    assert (
        orchestrator_may_sign_off(_run(flag, auto_approve=auto), stage, auto_approve=auto)
        is expected
    )


@pytest.fixture(name="gated")
def gated_fixture(db_session: Session, tmp_path):
    """A ticket parked at the design stage's sign-off gate under a live run."""
    workspace = Workspace(slug=f"dpg-{uuid4()}", name="DPG", repo_path=str(tmp_path))
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    template = WorkflowTemplate(
        slug=f"dpg-tpl-{uuid4()}",
        name="DPG",
        stages_json=json.dumps([s.model_dump(mode="json") for s in STAGES]),
        transitions_json="[]",
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)
    ticket = Ticket(
        external_id=f"dpg-{uuid4()}",
        workspace_id=workspace.id,
        title="Plan it",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key=DESIGN.key,
        workflow_stage_status=StageStatus.AWAITING,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.add(
        WorkflowInstance(
            ticket_id=ticket.id,
            template_id=template.id,
            current_stage_key=DESIGN.key,
            stages_json=initial_stages_json(STAGES),
        )
    )
    db_session.commit()

    def park(stage_key: str, *, approve_design_plans: bool) -> tuple[OrchestrationRun, Approval]:
        run = OrchestrationRun(
            run_code=f"orch_{uuid4().hex[:6]}",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            approve_design_plans=approve_design_plans,
            status=OrchestrationRunStatus.RUNNING,
        )
        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            kind=ApprovalKind.WORKFLOW_GATE,
            stage_key=stage_key,
            status=ApprovalStatus.PENDING,
            title="sign off",
        )
        db_session.add_all([run, approval])
        db_session.commit()
        db_session.refresh(run)
        db_session.refresh(approval)
        return run, approval

    return ticket, park


def test_the_run_signs_off_a_design_plan_and_says_so(db_session, gated):
    ticket, park = gated
    run, approval = park(DESIGN.key, approve_design_plans=True)

    assert resolve_gate_if_permitted(db_session, ticket, run, DESIGN, auto_approve=False)

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.APPROVED
    assert approval.resolved_by == "automation"
    assert decision_kinds(db_session, ticket.id) == ["approved_design_plan"]


def test_unticked_the_gate_waits_for_a_person(db_session, gated):
    ticket, park = gated
    run, approval = park(DESIGN.key, approve_design_plans=False)

    assert not resolve_gate_if_permitted(db_session, ticket, run, DESIGN, auto_approve=False)

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.PENDING
    assert decision_kinds(db_session, ticket.id) == []


def test_the_flag_never_touches_another_gate(db_session, gated):
    ticket, park = gated
    run, approval = park(IMPLEMENT.key, approve_design_plans=True)

    assert not resolve_gate_if_permitted(db_session, ticket, run, IMPLEMENT, auto_approve=False)

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.PENDING


def test_auto_approve_signs_off_without_claiming_a_design_decision(db_session, gated):
    ticket, park = gated
    run, approval = park(IMPLEMENT.key, approve_design_plans=True)

    assert resolve_gate_if_permitted(db_session, ticket, run, IMPLEMENT, auto_approve=True)

    db_session.refresh(approval)
    assert approval.status is ApprovalStatus.APPROVED
    assert decision_kinds(db_session, ticket.id) == []  # auto_approve is its own audit trail


_PASS_REPORT = (
    "done\n<<<LOREGARDEN_STAGE_REPORT>>>\n"
    + json.dumps({"status": "pass", "confidence": 0.9})
    + "\n<<<END_STAGE_REPORT>>>\n"
)


@pytest.mark.parametrize("flag", [True, False])
def test_the_post_run_gate_follows_the_flag(db_session, gated, flag):
    """The path a real gate_required stage takes: the run succeeds,
    run_completion raises the sign-off, and the parent run's dial decides."""
    ticket, _ = gated
    ticket.workflow_stage_status = StageStatus.RUNNING
    parent = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        approve_design_plans=flag,
        status=OrchestrationRunStatus.RUNNING,
    )
    db_session.add_all([ticket, parent])
    db_session.commit()
    db_session.refresh(parent)
    run = AgentRun(
        run_code=f"r_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=DESIGN.agent_id,
        stage_key=DESIGN.key,
        status=RunStatus.RUNNING,
        orchestration_run_id=parent.id,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)

    OrchestrationService(db_session).complete_run(
        run, status=RunStatus.SUCCEEDED, stdout=_PASS_REPORT
    )

    gate = db_session.exec(
        select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == DESIGN.key)
    ).one()
    expected = ApprovalStatus.APPROVED if flag else ApprovalStatus.PENDING
    assert gate.status is expected
    assert decision_kinds(db_session, ticket.id) == (["approved_design_plan"] if flag else [])


def _live_shapes() -> list[dict]:
    """The stage keys 0133 targets, as the live templates spell them."""
    return [
        {"key": "plan-synthesis", "name": "Plan synthesis", "order": 1, "agent_id": "planner"},
        {"key": "ui-design", "name": "UI Design", "order": 2, "agent_id": "ui-design-decision"},
        {"key": "implement", "name": "Implement", "order": 3, "agent_id": "backend_implementer"},
    ]


def test_migration_gates_the_live_templates_and_their_drafts(isolated_db):
    slug = "studio-loregarden-tdd-v3"
    with Session(isolated_db) as session:
        session.add(
            WorkflowTemplate(
                id="tpl", slug=slug, name="v3", stages_json=json.dumps(_live_shapes()), version=3
            )
        )
        session.execute(
            text(
                "INSERT INTO studio_workflows (id, slug, name, description, stages_json, "
                "transitions_json, created_at, updated_at) VALUES ('d', :s, 'v3', '', :st, '[]', "
                "'2026-01-01', '2026-01-01')"
            ),
            {"s": slug, "st": json.dumps(_live_shapes())},
        )
        session.commit()

    with isolated_db.begin() as conn:
        m_design_plan_gates(conn)
        m_design_plan_gates(conn)  # append-only migrations are re-runnable

    with Session(isolated_db) as session:
        live = session.exec(select(WorkflowTemplate).where(WorkflowTemplate.slug == slug)).one()
        gated = {s["key"] for s in json.loads(live.stages_json) if s.get("gate_required")}
        assert gated == set(DESIGN_PLAN_STAGES[slug])
        assert live.version == 4  # bumped once, not twice
        draft = session.execute(
            text("SELECT stages_json FROM studio_workflows WHERE slug=:s"), {"s": slug}
        ).scalar_one()
        assert {s["key"] for s in json.loads(draft) if s.get("gate_required")} == gated
