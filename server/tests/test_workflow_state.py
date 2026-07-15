import json

from fastapi.testclient import TestClient
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
    WorkflowInstance,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.seed import seed_database
from loregarden.services.workflow_service import resolve_workspace_stages
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool


def _ticket_id_by_external_id(client: TestClient, external_id: str) -> str:
    for t in client.get("/api/tickets").json():
        if t["external_id"] == external_id:
            return t["id"]
    raise AssertionError(f"ticket not found: {external_id}")


def _ticket_detail(client: TestClient, ticket_id: str) -> dict:
    res = client.get(f"/api/tickets/{ticket_id}")
    assert res.status_code == 200
    return res.json()


def _stage_statuses(detail: dict) -> dict[str, str]:
    return {s["key"]: s["status"] for s in detail["stages"]}


def test_seeded_ticket_states_agree(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    stages = _stage_statuses(detail)

    assert detail["state"] == "in_progress"
    assert detail["workflow_stage_key"] == "implementation"
    assert detail["workflow_stage_status"] == "done"
    assert stages["implementation"] == "done"
    assert stages["planning"] == "done"
    assert stages["testing"] == "pending"
    assert stages[detail["workflow_stage_key"]] == detail["workflow_stage_status"]


def test_start_run_syncs_all_layers(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    started = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert started.status_code == 200
    body = _ticket_detail(client, ticket_id)
    stages = _stage_statuses(body)

    assert body["state"] == "in_progress"
    assert body["workflow_stage_key"] == "implementation"
    assert body["workflow_stage_status"] == "done"
    assert stages["implementation"] == "done"
    assert stages[body["workflow_stage_key"]] == body["workflow_stage_status"]


def test_failed_run_blocks_ticket_and_stage(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    _ticket_detail(client, ticket_id)
    res = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert res.status_code == 200
    body = _ticket_detail(client, ticket_id)
    stages = _stage_statuses(body)

    assert body["state"] == "blocked"
    assert body["workflow_stage_status"] == "blocked"
    assert stages[body["workflow_stage_key"]] == body["workflow_stage_status"]


def test_rerun_blocked_stage_after_failure(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    stage_key = _ticket_detail(client, ticket_id)["workflow_stage_key"]
    failed = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert failed.status_code == 200
    blocked = _ticket_detail(client, ticket_id)
    assert blocked["state"] == "blocked"
    assert blocked["workflow_stage_status"] == "blocked"

    monkeypatch.delenv("LOREGARDEN_FORCE_AGENT_FAIL", raising=False)
    rerun = client.post(
        f"/api/tickets/{ticket_id}/start",
        json={"manual": True, "stage_key": stage_key},
    )
    assert rerun.status_code == 200
    after = _ticket_detail(client, ticket_id)
    assert after["workflow_stage_status"] == "done"
    assert after["state"] == "in_progress"
    assert after["blocking_issues"] == ""
    assert _stage_statuses(after)[stage_key] == "done"


def test_starting_a_pending_stage_clears_stale_blocking_issues(
    client: TestClient, db_session: Session
):
    """A stage can be left PENDING (not BLOCKED) while blocking_issues still
    holds a message from an unrelated earlier failure — e.g. a parallel-stage
    reroute elsewhere left the text behind without marking this stage BLOCKED.
    Starting a fresh run must not immediately misreport the ticket as BLOCKED
    just because that stale message is still sitting there.
    """
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    ticket = db_session.get(Ticket, ticket_id)
    ticket.blocking_issues = "Stale message from an unrelated earlier failure."
    db_session.add(ticket)
    db_session.commit()

    OrchestrationService(db_session).start_run(ticket, stage_key="implementation")
    db_session.refresh(ticket)

    assert ticket.blocking_issues == ""
    assert ticket.state != TicketState.BLOCKED


def test_opening_a_human_gate_clears_stale_blocking_issues(client: TestClient, db_session: Session):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    ticket = db_session.get(Ticket, ticket_id)
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    ws = db_session.get(Workspace, ticket.workspace_id)
    _, stages = resolve_workspace_stages(db_session, ws)
    set_stage_status(ticket, instance, stages, "approval", StageStatus.PENDING)
    ticket.blocking_issues = "Stale message from an unrelated earlier failure."
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()

    OrchestrationService(db_session).enter_human_gate(ticket, stage_key="approval")
    db_session.refresh(ticket)

    assert ticket.blocking_issues == ""
    assert ticket.state != TicketState.BLOCKED
    assert ticket.workflow_stage_status == StageStatus.AWAITING


def test_advance_stage_moves_cursor_and_keeps_steps_consistent(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    advanced = client.post(f"/api/tickets/{ticket_id}/advance", json={})
    assert advanced.status_code == 200
    body = advanced.json()
    stages = _stage_statuses(body)

    assert body["workflow_stage_key"] == "testing"
    assert body["workflow_stage_status"] == "pending"
    assert stages["implementation"] == "done"
    assert stages["testing"] == "pending"
    assert stages[body["workflow_stage_key"]] == body["workflow_stage_status"]


def test_manual_ticket_state_update(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "02-bootstrap-react-ide-shell")
    updated = client.patch(
        f"/api/tickets/{ticket_id}",
        json={"state": "wont_do", "auto_state": False},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["state"] == "wont_do"
    assert body["state_locked"] is True

    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["state"] == "wont_do"


def test_manual_title_and_description_update(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "02-bootstrap-react-ide-shell")
    before = client.get(f"/api/tickets/{ticket_id}").json()
    revision_before = before["revision"]

    updated = client.patch(
        f"/api/tickets/{ticket_id}",
        json={"title": "Updated ticket title", "description": "Updated description"},
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["title"] == "Updated ticket title"
    assert body["description"] == "Updated description"
    assert body["revision"] == revision_before + 1
    assert body["last_updated_by"] == "human"

    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["title"] == "Updated ticket title"
    assert detail["description"] == "Updated description"


def test_empty_title_update_rejected(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "02-bootstrap-react-ide-shell")
    res = client.patch(f"/api/tickets/{ticket_id}", json={"title": "   "})
    assert res.status_code == 400


def test_wont_do_blocks_start_run(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "02-bootstrap-react-ide-shell")
    client.patch(f"/api/tickets/{ticket_id}", json={"state": "wont_do"})
    res = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True})
    assert res.status_code == 400


def test_manual_workflow_stage_update(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    updated = client.patch(
        f"/api/tickets/{ticket_id}",
        json={"stage_key": "testing", "stage_status": "done"},
    )
    assert updated.status_code == 200
    stages = {s["key"]: s["status"] for s in updated.json()["stages"]}
    assert stages["testing"] == "done"


def test_wont_do_persists_after_reconcile(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "03-wire-cli-agent-runner")
    client.patch(f"/api/tickets/{ticket_id}", json={"state": "wont_do"})
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    assert detail["state"] == "wont_do"
    assert detail["state_locked"] is True


def test_bulk_stage_updates(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    updated = client.patch(
        f"/api/tickets/{ticket_id}",
        json={
            "stage_updates": {
                "planning": "done",
                "context": "done",
                "specification": "done",
            }
        },
    )
    assert updated.status_code == 200
    stages = {s["key"]: s["status"] for s in updated.json()["stages"]}
    assert stages["planning"] == "done"
    assert stages["context"] == "done"
    assert stages["specification"] == "done"


def test_stage_wont_do_skips_step_and_blocks_run(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    updated = client.patch(
        f"/api/tickets/{ticket_id}",
        json={"stage_key": "testing", "stage_status": "wont_do"},
    )
    assert updated.status_code == 200
    body = updated.json()
    stages = _stage_statuses(body)

    assert stages["testing"] == "wont_do"
    assert body["state"] == "in_progress"

    res = client.post(
        f"/api/tickets/{ticket_id}/start",
        json={"manual": True, "stage_key": "testing"},
    )
    assert res.status_code == 400
    assert "won't do" in res.json()["detail"].lower()


def test_stage_wont_do_counts_toward_required_completion(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    detail = client.get(f"/api/tickets/{ticket_id}").json()
    stage_keys = [s["key"] for s in detail["stages"]]

    updated = client.patch(
        f"/api/tickets/{ticket_id}",
        json={
            "stage_updates": {
                key: ("wont_do" if key == "testing" else "done") for key in stage_keys
            },
            "auto_state": True,
        },
    )
    assert updated.status_code == 200
    body = updated.json()
    assert body["state"] == "done"
    assert all(s["status"] in ("done", "wont_do") for s in body["stages"])


def test_advance_from_wont_do_stage(client: TestClient):
    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    client.patch(
        f"/api/tickets/{ticket_id}",
        json={"stage_key": "implementation", "stage_status": "wont_do"},
    )
    advanced = client.post(f"/api/tickets/{ticket_id}/advance", json={})
    assert advanced.status_code == 200
    body = advanced.json()
    stages = _stage_statuses(body)

    assert stages["implementation"] == "wont_do"
    assert body["workflow_stage_key"] == "testing"
    assert body["workflow_stage_status"] == "pending"


def test_stale_blocking_issues_do_not_block_after_stage_done():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        instance = session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        ws = session.get(Workspace, ticket.workspace_id)
        _, stages = resolve_workspace_stages(session, ws)

        ticket.blocking_issues = (
            "Agent run interrupted before completion (server reload or worker stopped). "
            "Re-run the stage to continue."
        )
        ticket.state = TicketState.BLOCKED
        set_stage_status(ticket, instance, stages, "testing", StageStatus.DONE)
        session.add(ticket)
        session.add(instance)
        session.commit()

        OrchestrationService(session).reconcile_ticket(ticket)
        session.refresh(ticket)

        assert ticket.workflow_stage_status == StageStatus.DONE
        assert ticket.state == TicketState.IN_PROGRESS
        assert ticket.blocking_issues  # message may remain until cleared on next success/advance


def test_reconcile_repairs_drifted_instance():
    engine = create_engine(
        "sqlite://",
        connect_args={"check_same_thread": False},
        poolclass=StaticPool,
    )
    SQLModel.metadata.create_all(engine)
    with Session(engine) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "02-bootstrap-react-ide-shell")
        ).first()
        instance = session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        ws = session.get(Workspace, ticket.workspace_id)
        ticket.workflow_stage_status = StageStatus.RUNNING
        ticket.state = TicketState.IN_PROGRESS
        instance.stages_json = json.dumps([{"key": "implementation", "status": "pending"}])
        session.add(ticket)
        session.add(instance)
        session.commit()

        OrchestrationService(session).reconcile_ticket(ticket)
        session.refresh(ticket)
        session.refresh(instance)

        _, stages = resolve_workspace_stages(session, ws)
        stage_map = parse_stage_map(instance, stages)

        assert ticket.workflow_stage_status == stage_map[ticket.workflow_stage_key]
        for stage in stages:
            view_status = stage_map[stage.key]
            assert view_status.value in {s["status"] for s in json.loads(instance.stages_json)}


def test_human_gate_stage_opens_approval_without_agent(client: TestClient, monkeypatch):
    from loregarden.db.session import engine

    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        instance = session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        ws = session.get(Workspace, ticket.workspace_id)
        _, stages = resolve_workspace_stages(session, ws)
        ticket.workflow_stage_key = "approval"
        ticket.workflow_stage_status = StageStatus.PENDING
        set_stage_status(ticket, instance, stages, "approval", StageStatus.PENDING)
        session.add(ticket)
        session.add(instance)
        session.commit()

    scheduled: list[str] = []
    monkeypatch.setattr(
        "loregarden.api.tickets.schedule_agent_run",
        lambda run_id: scheduled.append(run_id),
    )

    res = client.post(
        f"/api/tickets/{ticket_id}/start",
        json={"manual": True, "stage_key": "approval"},
    )
    assert res.status_code == 200
    assert scheduled == []
    body = res.json()
    assert body["workflow_stage_key"] == "approval"
    assert body["workflow_stage_status"] == "awaiting"
    assert _stage_statuses(body)["approval"] == "awaiting"
    assert body["artifacts"]["live"] == "Awaiting your approval in Triage or Inbox…"

    with Session(engine) as session:
        approvals = session.exec(
            select(Approval).where(
                Approval.ticket_id == ticket_id,
                Approval.stage_key == "approval",
                Approval.kind == ApprovalKind.WORKFLOW_GATE,
                Approval.status == ApprovalStatus.PENDING,
            )
        ).all()
        assert len(approvals) == 1
        failed_runs = session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket_id,
                AgentRun.stage_key == "approval",
                AgentRun.status == RunStatus.FAILED,
            )
        ).all()
        assert failed_runs == []


def test_done_stage_completes_ticket_without_agent(client: TestClient, monkeypatch):
    from loregarden.db.session import engine

    ticket_id = _ticket_id_by_external_id(client, "04-workflow-template-overrides")
    with Session(engine) as session:
        ticket = session.get(Ticket, ticket_id)
        instance = session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        ws = session.get(Workspace, ticket.workspace_id)
        _, stages = resolve_workspace_stages(session, ws)
        for key in (
            "planning",
            "context",
            "specification",
            "test_design",
            "test_break",
            "implementation",
            "testing",
            "review",
            "approval",
        ):
            if key in {s.key for s in stages}:
                set_stage_status(ticket, instance, stages, key, StageStatus.DONE)
        ticket.workflow_stage_key = "done"
        ticket.workflow_stage_status = StageStatus.PENDING
        set_stage_status(ticket, instance, stages, "done", StageStatus.PENDING)
        session.add(ticket)
        session.add(instance)
        session.commit()

    scheduled: list[str] = []
    monkeypatch.setattr(
        "loregarden.api.tickets.schedule_agent_run",
        lambda run_id: scheduled.append(run_id),
    )

    res = client.post(
        f"/api/tickets/{ticket_id}/start",
        json={"manual": True, "stage_key": "done"},
    )
    assert res.status_code == 200
    assert scheduled == []
    body = res.json()
    assert body["state"] == "done"
    assert body["workflow_stage_key"] == "done"
    assert body["workflow_stage_status"] == "done"
    assert _stage_statuses(body)["done"] == "done"
