"""Human testing approval gate: impact summary + workflow-defined checklist."""

import json

from fastapi.testclient import TestClient
from loregarden.models.domain import Approval, StageStatus, Ticket, WorkflowInstance, Workspace
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_service import resolve_workspace_stages
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, select


def _create_blobert_ticket(client: TestClient, db_session: Session) -> Ticket:
    ws_resp = client.post(
        "/api/workspaces",
        json={
            "slug": "blobert-test",
            "name": "Blobert Test",
            "workflow_template_slug": "blobert-tdd",
        },
    )
    assert ws_resp.status_code == 201, ws_resp.text

    ticket_resp = client.post(
        "/api/tickets",
        json={
            "workspace_slug": "blobert-test",
            "title": "Dash movement and cooldown",
            "work_item_type": "milestone",
            "description": "Add a dash ability with a cooldown timer.",
            "acceptance_criteria": ["Dash moves the player", "Cooldown blocks re-triggering"],
        },
    )
    assert ticket_resp.status_code == 201, ticket_resp.text

    ticket = db_session.get(Ticket, ticket_resp.json()["id"])
    assert ticket is not None
    return ticket


def test_stored_raw_placeholder_is_expanded_on_read(client: TestClient, db_session: Session):
    """A gate recorded while the workflow yaml and expansion code were out of sync
    stores a raw {{acceptance_criteria}} token. It must never reach the UI.
    """
    ticket = _create_blobert_ticket(client, db_session)
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    assert instance is not None
    workspace = db_session.get(Workspace, ticket.workspace_id)
    _, stages = resolve_workspace_stages(db_session, workspace)

    ticket.workflow_stage_key = "ac_gate"
    set_stage_status(ticket, instance, stages, "ac_gate", StageStatus.DONE)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    db_session.refresh(ticket)

    OrchestrationService(db_session).enter_human_gate(ticket, stage_key="playtest")

    approval = db_session.exec(
        select(Approval).where(Approval.ticket_id == ticket.id, Approval.stage_key == "playtest")
    ).one()
    approval.checklist_json = json.dumps(["Load the scene", "{{acceptance_criteria}}"])
    db_session.add(approval)
    db_session.commit()

    resp = client.get("/api/inbox/approvals", params={"ticket_id": ticket.id})
    assert resp.status_code == 200
    view = next(a for a in resp.json() if a["stage_key"] == "playtest")

    assert "{{acceptance_criteria}}" not in view["checklist"]
    assert view["checklist"] == [
        "Load the scene",
        "Play-test by hand — Dash moves the player",
        "Play-test by hand — Cooldown blocks re-triggering",
    ]


def test_playtest_human_gate_includes_test_summary_and_checklist(
    client: TestClient, db_session: Session
):
    ticket = _create_blobert_ticket(client, db_session)
    instance = db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first()
    assert instance is not None
    workspace = db_session.get(Workspace, ticket.workspace_id)
    _, stages = resolve_workspace_stages(db_session, workspace)

    ticket.workflow_stage_key = "ac_gate"
    set_stage_status(ticket, instance, stages, "ac_gate", StageStatus.DONE)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.commit()
    db_session.refresh(ticket)

    svc = OrchestrationService(db_session)
    svc.enter_human_gate(ticket, stage_key="playtest")

    resp = client.get("/api/inbox/approvals", params={"ticket_id": ticket.id})
    assert resp.status_code == 200
    approvals = [a for a in resp.json() if a["stage_key"] == "playtest"]
    assert len(approvals) == 1
    approval = approvals[0]

    assert "Dash movement and cooldown" in approval["impact"]
    assert "Add a dash ability with a cooldown timer." in approval["impact"]
    assert "Dash moves the player" in approval["impact"]

    # The generic "walk through the acceptance criteria" bullet is replaced by
    # one concrete play-test item per acceptance criterion, so each gate lists
    # what this specific change needs exercised.
    assert approval["checklist"] == [
        "Create or update the test level scene(s) needed to exercise this change",
        "Load the affected scene(s) in the Godot editor and run them",
        "Play-test by hand — Dash moves the player",
        "Play-test by hand — Cooldown blocks re-triggering",
        "Check for regressions in adjacent systems the change touches",
        "Confirm no console errors/warnings appear during play",
    ]
