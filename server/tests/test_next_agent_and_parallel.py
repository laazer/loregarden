import json

from fastapi.testclient import TestClient
from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    ClassifyRoute,
    Ticket,
    TicketState,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
    WorkItemType,
    Workspace,
)
from loregarden.services.studio_routing import resolve_classify_route, resolve_stage_execution
from sqlmodel import Session, select


def test_resolve_classify_route_prefers_next_agent():
    ticket = Ticket(
        id="t1",
        external_id="01-test",
        workspace_id="ws",
        title="Generic feature",
        description="No language keywords here",
        next_agent="frontend_implementer",
    )
    stage = WorkflowStageDef(
        key="route_impl",
        name="Route Implementation",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(
                languages=["python"],
                specialties=["backend"],
                agent_id="backend_implementer",
                skill_name="apply_patch",
                default=True,
            ),
            ClassifyRoute(
                languages=["typescript"],
                specialties=["frontend"],
                agent_id="frontend_implementer",
                skill_name="apply_patch",
            ),
        ],
    )
    agent_id, skill = resolve_classify_route(ticket, stage)
    assert agent_id == "frontend_implementer"
    assert skill == "apply_patch"


def test_resolve_classify_route_ignores_unknown_next_agent():
    ticket = Ticket(
        id="t1",
        external_id="01-test",
        workspace_id="ws",
        title="Python backend API",
        description="Implement python backend route",
        next_agent="not_a_real_agent",
    )
    stage = WorkflowStageDef(
        key="route_impl",
        name="Route Implementation",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(
                languages=["python"],
                specialties=["backend"],
                agent_id="backend_implementer",
                skill_name="apply_patch",
                default=True,
            ),
        ],
    )
    agent_id, skill = resolve_classify_route(ticket, stage)
    assert agent_id == "backend_implementer"


def test_resolve_classify_route_prefers_content_over_stale_next_agent():
    """Regression for #164: a stale next_agent hint from an earlier, unrelated
    stage must not permanently override a ticket whose text now clearly
    classifies to a different specialist.
    """
    ticket = Ticket(
        id="t1",
        external_id="164-test",
        workspace_id="ws",
        title="Backend routing fix",
        description="Fix the server endpoint and database migration for this API route",
        next_agent="frontend_implementer",
    )
    stage = WorkflowStageDef(
        key="implement",
        name="Implement",
        stage_type="classify",
        classify_routes=[
            ClassifyRoute(
                languages=["python"],
                specialties=["backend"],
                agent_id="backend_implementer",
                skill_name="apply_patch",
                default=True,
            ),
            ClassifyRoute(
                languages=["typescript"],
                specialties=["frontend"],
                agent_id="frontend_implementer",
                skill_name="apply_patch",
            ),
        ],
    )
    agent_id, skill = resolve_classify_route(ticket, stage)
    assert agent_id == "backend_implementer"
    assert skill == "apply_patch"


def test_resolve_stage_execution_honors_next_agent_on_implementation():
    ticket = Ticket(
        id="t1",
        external_id="01-test",
        workspace_id="ws",
        title="Gameplay feature",
        next_agent="frontend_implementer",
    )
    stage = WorkflowStageDef(
        key="implementation",
        name="Implementation",
        agent_id="backend_implementer",
        skill_name="apply_patch",
    )
    agent_id, skill = resolve_stage_execution(ticket, stage)
    assert agent_id == "frontend_implementer"
    assert skill == "apply_patch"


def test_resolve_stage_execution_ignores_stale_next_agent_on_linear_stage():
    """A fully-specified linear stage keeps its template agent even when a stale
    ticket.next_agent (left over from a previous stage, e.g. on a standalone
    start with no advance/reconcile to refresh it) names a different agent.
    Regression for run_43ea0c: the `learning` stage ran under `ac_gatekeeper`."""
    ticket = Ticket(
        id="t1",
        external_id="17-learning",
        workspace_id="ws",
        title="Learning stage",
        next_agent="ac_gatekeeper",  # stale hint from the prior AC-gate stage
    )
    stage = WorkflowStageDef(
        key="learning",
        name="Learning",
        agent_id="learning",
        skill_name="learning",
    )
    agent_id, skill = resolve_stage_execution(ticket, stage)
    assert agent_id == "learning"
    assert skill == "learning"


def test_parallel_stage_runs_all_agents(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)

    # Orchestration profiles resolve from settings.repo_root, not from this
    # workspace's repo_path — redirect it to an empty tmp dir so profile lookup
    # falls through to the hardcoded default (builtin_autopilot, gates
    # disabled) instead of loregarden's own real profile, whose gate commands
    # (`cd server && ruff check .`, etc.) assume a real checkout and would
    # always fail against this throwaway repo.
    monkeypatch.setattr(settings, "repo_root", tmp_path)

    template = WorkflowTemplate(
        slug="test-parallel-review",
        name="Parallel Review Test",
        description="test",
        stages_json=json.dumps(
            [
                {
                    "key": "script_review",
                    "name": "Script Review",
                    "stage_type": "parallel",
                    "order": 1,
                    "parallel_agents": [
                        {"agent_id": "static_qa", "skill_name": "static_qa"},
                        {"agent_id": "gatekeeper", "skill_name": "ac_gate"},
                    ],
                },
                {"key": "done", "name": "Done", "agent_id": "", "skill_name": "", "order": 2},
            ]
        ),
        transitions_json=json.dumps([{"from": "script_review", "to": "done"}]),
        source_path="test:parallel",
    )
    db_session.add(template)
    db_session.commit()
    db_session.refresh(template)

    ws = Workspace(
        slug="parallel-test-ws",
        name="Parallel Test",
        repo_path=str(tmp_path),
        # No profile file exists for this slug (or any slug) under the
        # redirected repo_root above, so profile resolution falls through to
        # the hardcoded default: builtin_autopilot driver, gates disabled.
        orchestration_profile_slug="parallel-test-ws",
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    ticket = Ticket(
        external_id="parallel-review-test",
        workspace_id=ws.id,
        title="Parallel review ticket",
        description="Runs two reviewers in parallel",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="script_review",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="script_review",
        stages_json=json.dumps(
            [
                {"key": "script_review", "status": "pending"},
                {"key": "done", "status": "pending"},
            ]
        ),
    )
    db_session.add(instance)
    db_session.commit()

    res = client.post(
        f"/api/tickets/{ticket.id}/orchestrate",
        json={"max_stages": 1},
    )
    assert res.status_code == 200
    body = res.json()
    assert body["state"] == "in_progress"
    assert body["workflow_stage_status"] == "done"

    stages = {item["key"]: item["status"] for item in body["stages"]}
    assert stages["script_review"] == "done"

    runs = list(db_session.exec(select(AgentRun).where(AgentRun.ticket_id == ticket.id)).all())
    assert {run.agent_id for run in runs} == {"static_qa", "gatekeeper"}


def test_orchestration_reroutes_when_gate_fails(
    client: TestClient, db_session: Session, tmp_path, monkeypatch
):
    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, capture_output=True, check=True)

    gate_dir = tmp_path / "ci" / "scripts"
    gate_dir.mkdir(parents=True)
    (gate_dir / "run_workflow_transition_gates.py").write_text(
        "import sys\nsys.exit(1)\n",
        encoding="utf-8",
    )

    # Orchestration profiles resolve from loregarden's own repo tree (never from
    # the workspace's repo_path) — redirect that to tmp_path so this test's
    # gate-test.yaml is actually the one resolve_orchestration_profile finds.
    monkeypatch.setattr(settings, "repo_root", tmp_path)
    orch_dir = tmp_path / "agent_context" / "orchestration"
    orch_dir.mkdir(parents=True)
    (orch_dir / "gate-test.yaml").write_text(
        "slug: gate-test\n"
        "driver: builtin_autopilot\n"
        "workflow_template: loregarden-tdd\n"
        "gates:\n"
        "  enabled: true\n",
        encoding="utf-8",
    )

    template = db_session.exec(
        select(WorkflowTemplate).where(WorkflowTemplate.slug == "loregarden-tdd")
    ).first()
    assert template is not None

    from loregarden.core.workflow_loader import get_template_stages
    from loregarden.services.workflow_state import initial_stages_json

    stages = get_template_stages(template)

    ws = Workspace(
        slug="gate-test-ws",
        name="Gate Test",
        repo_path=str(tmp_path),
        workflow_template_id=template.id,
        orchestration_profile_slug="gate-test",
    )
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)

    ticket = Ticket(
        external_id="gate-block-test",
        workspace_id=ws.id,
        title="Gate reroute ticket",
        description="Should reroute back to planning after a failing transition gate",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.TASK,
        workflow_stage_key="planning",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)

    instance = WorkflowInstance(
        ticket_id=ticket.id,
        template_id=template.id,
        current_stage_key="planning",
        stages_json=initial_stages_json(stages),
    )
    db_session.add(instance)
    db_session.commit()

    res = client.post(
        f"/api/tickets/{ticket.id}/orchestrate",
        json={"max_stages": 1},
    )
    assert res.status_code == 200
    body = res.json()
    # A failing transition gate isn't the agent reporting bad work — it's this
    # stage's own output failing an objective check, so it self-redoes the same
    # stage instead of hard-blocking for a human.
    assert body["state"] != "blocked"
    assert body["workflow_stage_key"] == "planning"
    assert body["blocking_issues"]
