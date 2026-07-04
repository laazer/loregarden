import json

from fastapi.testclient import TestClient
from sqlmodel import Session, SQLModel, create_engine, select
from sqlmodel.pool import StaticPool

from loregarden.models.domain import StageStatus, Ticket, TicketState, WorkflowInstance, Workspace
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.seed import seed_database
from loregarden.services.workflow_state import parse_stage_map
from loregarden.services.workflow_service import resolve_workspace_stages


def _ticket_id_by_external_id(client: TestClient, external_id: str) -> str:
    for t in client.get("/api/tickets").json():
        if t["external_id"] == external_id:
            return t["id"]
    raise AssertionError(f"ticket not found: {external_id}")


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
    body = started.json()
    stages = _stage_statuses(body)

    assert body["state"] == "in_progress"
    assert body["workflow_stage_key"] == "implementation"
    assert body["workflow_stage_status"] == "done"
    assert stages["implementation"] == "done"
    assert stages[body["workflow_stage_key"]] == body["workflow_stage_status"]


def test_failed_run_blocks_ticket_and_stage(client: TestClient, monkeypatch):
    monkeypatch.setenv("LOREGARDEN_FORCE_AGENT_FAIL", "1")
    ticket_id = _ticket_id_by_external_id(client, "01-bootstrap-fastapi-control-plane")
    body = client.post(f"/api/tickets/{ticket_id}/start", json={"manual": True}).json()
    stages = _stage_statuses(body)

    assert body["state"] == "blocked"
    assert body["workflow_stage_status"] == "blocked"
    assert stages[body["workflow_stage_key"]] == body["workflow_stage_status"]


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
        instance.stages_json = json.dumps(
            [{"key": "implementation", "status": "pending"}]
        )
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
            assert view_status.value in {
                s["status"] for s in json.loads(instance.stages_json)
            }
