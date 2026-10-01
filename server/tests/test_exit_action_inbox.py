"""HTTP regressions for exit-action inbox resolution (AC-8/AC-9/AC-12).

Bare ``action=recheck`` must not 500 on an empty client body, and approve of a
grantable-authority gate must schedule a continuation rather than mark the
stage done.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import (
    AgentRun,
    Approval,
    ApprovalKind,
    ApprovalStatus,
    RunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_state import parse_stage_map, set_stage_status
from sqlmodel import Session, select


def _seed_recheck_gate(session: Session) -> Approval:
    ticket = session.exec(select(Ticket)).first()
    assert ticket is not None
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        title="Resolve runtime action",
        stage_key=ticket.workflow_stage_key,
        tool_input_json=json.dumps(
            {
                "human_required_actions": [
                    {
                        "action_key": "read-usage",
                        "action_label": "Read provider usage",
                        "action_description": "",
                        "requirement": {
                            "kind": "credential",
                            "credential_key": "claude_profile",
                        },
                        "reason_code": "credential_unavailable",
                        "reason": "Credential unavailable: claude_profile",
                        "resolution_mode": "recheck",
                    }
                ],
                "allowed_actions": ["recheck", "reject"],
            }
        ),
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)
    return approval


def _seed_authority_gate(session: Session) -> Approval:
    ticket = session.exec(select(Ticket)).first()
    assert ticket is not None
    approval = Approval(
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        kind=ApprovalKind.WORKFLOW_GATE,
        title="Resolve authority action",
        stage_key=ticket.workflow_stage_key,
        tool_input_json=json.dumps(
            {
                "human_required_actions": [
                    {
                        "action_key": "publish-release",
                        "action_label": "Publish the release",
                        "action_description": "",
                        "requirement": {
                            "kind": "authority",
                            "authority_scope": "release:publish",
                        },
                        "reason_code": "authority_grant_required",
                        "reason": "Authority grant required: release:publish",
                        "resolution_mode": "approve",
                    }
                ],
                "allowed_actions": ["approve", "reject"],
            }
        ),
        status=ApprovalStatus.PENDING,
    )
    session.add(approval)
    session.commit()
    session.refresh(approval)
    return approval


@pytest.fixture(name="gated_ticket")
def gated_ticket_fixture(db_session: Session) -> Ticket:
    """The seeded ticket, parked AWAITING on a stage an agent already ran."""
    ticket = db_session.exec(select(Ticket)).first()
    assert ticket is not None
    orch = OrchestrationService(db_session)
    orch.ensure_workflow_instance(ticket)
    instance, stages = orch._resolve_stages(ticket)
    stage = next(s for s in stages if s.agent_id)
    ticket.workflow_stage_key = stage.key
    set_stage_status(ticket, instance, stages, stage.key, StageStatus.AWAITING)
    db_session.add(ticket)
    db_session.add(instance)
    db_session.add(
        AgentRun(
            run_code="R-gated",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id=stage.agent_id,
            stage_key=stage.key,
            status=RunStatus.SUCCEEDED,
        )
    )
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _stage_status(db_session: Session, ticket: Ticket) -> StageStatus:
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    return parse_stage_map(instance, stages)[ticket.workflow_stage_key]


def test_inbox_bare_recheck_probes_server_side_snapshot(
    client: TestClient, db_session: Session, gated_ticket: Ticket
):
    """AC-8/AC-12: POST action=recheck with no body.answers must not 500."""
    approval = _seed_recheck_gate(db_session)

    with (
        patch(
            "loregarden.services.exit_action_dispatch.probe_credentials",
            return_value={"claude_profile": "available"},
        ),
        patch("loregarden.services.run_service.schedule_orchestration") as scheduled,
    ):
        response = client.post(
            f"/api/inbox/approvals/{approval.id}",
            json={"action": "recheck"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == approval.id
    # Cleared, so the gate closed and the stage continues with the action.
    assert body["status"] == "approved"
    assert body["newly_assigned_action_keys"] == ["read-usage"]
    scheduled.assert_called_once()


def test_inbox_recheck_ignores_client_answers(client: TestClient, db_session: Session):
    """Client answers must not become the runtime snapshot used for recheck."""
    approval = _seed_recheck_gate(db_session)

    with (
        patch(
            "loregarden.services.exit_action_dispatch.probe_credentials",
            return_value={"claude_profile": "unavailable"},
        ),
        patch("loregarden.services.run_service.schedule_orchestration") as scheduled,
    ):
        response = client.post(
            f"/api/inbox/approvals/{approval.id}",
            json={
                "action": "recheck",
                # Valid ApprovalAction.answers shape — would falsely clear the
                # gate if recheck still treated answers as a runtime snapshot.
                "answers": {"claude_profile": "available"},
            },
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["status"] == "pending"
    assert body["newly_assigned_action_keys"] == []
    scheduled.assert_not_called()
    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING
    remaining = json.loads(approval.tool_input_json)["human_required_actions"]
    assert remaining[0]["action_key"] == "read-usage"


def test_a_recheck_that_clears_everything_leaves_nothing_to_approve(
    client: TestClient, db_session: Session, gated_ticket: Ticket, monkeypatch
):
    """AC-8/AC-9: once a recheck clears the gate, approve cannot complete the stage.

    The action has not run: only the continuation's passing report may attest
    it, so the stage must not reach DONE on an inbox click. The credential is
    supplied the way the probe really finds it, through the environment.
    """
    approval = _seed_recheck_gate(db_session)
    monkeypatch.setenv("CLAUDE_CODE_OAUTH_TOKEN", "test-oauth-token")

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        rechecked = client.post(f"/api/inbox/approvals/{approval.id}", json={"action": "recheck"})
        approved = client.post(f"/api/inbox/approvals/{approval.id}", json={"action": "approve"})

    assert rechecked.status_code == 200, rechecked.text
    assert approved.status_code == 400, approved.text
    scheduled.assert_called_once()
    db_session.refresh(gated_ticket)
    # Out of AWAITING so the continuation dispatches; never DONE.
    assert _stage_status(db_session, gated_ticket) == StageStatus.PENDING
    assert (
        db_session.exec(
            select(Approval).where(
                Approval.ticket_id == gated_ticket.id,
                Approval.status == ApprovalStatus.PENDING,
                Approval.kind == ApprovalKind.WORKFLOW_GATE,
            )
        ).all()
        == []
    )


@pytest.mark.parametrize(
    "payload",
    [
        pytest.param("{not json", id="malformed"),
        pytest.param("{}", id="empty"),
        pytest.param('{"human_required_actions": "nope"}', id="wrong-shape"),
    ],
)
def test_an_unreadable_or_empty_gate_refuses_approve(
    client: TestClient, db_session: Session, gated_ticket: Ticket, payload: str
):
    """AC-8: a gate the server cannot read permits nothing — it does not fail open."""
    approval = _seed_recheck_gate(db_session)
    approval.tool_input_json = payload
    db_session.add(approval)
    db_session.commit()

    response = client.post(f"/api/inbox/approvals/{approval.id}", json={"action": "approve"})

    assert response.status_code == 400, response.text
    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING
    assert _stage_status(db_session, gated_ticket) == StageStatus.AWAITING


def test_a_forged_allowed_actions_list_does_not_permit_approve(
    client: TestClient, db_session: Session, gated_ticket: Ticket
):
    """AC-8: what approve may do is recomputed from the actions, not read back."""
    approval = _seed_recheck_gate(db_session)
    payload = json.loads(approval.tool_input_json)
    payload["allowed_actions"] = ["approve", "reject"]
    approval.tool_input_json = json.dumps(payload)
    db_session.add(approval)
    db_session.commit()

    response = client.post(f"/api/inbox/approvals/{approval.id}", json={"action": "approve"})

    assert response.status_code == 400, response.text
    assert "recheck" in response.json()["detail"]
    assert _stage_status(db_session, gated_ticket) == StageStatus.AWAITING


def test_inbox_approve_authority_schedules_continuation(
    client: TestClient, db_session: Session, gated_ticket: Ticket
):
    """AC-9: approve of grantable authority continues the stage, never completes it."""
    approval = _seed_authority_gate(db_session)

    with patch("loregarden.services.run_service.schedule_orchestration") as scheduled:
        response = client.post(
            f"/api/inbox/approvals/{approval.id}",
            json={"action": "approve"},
        )

    assert response.status_code == 200, response.text
    body = response.json()
    assert body["newly_assigned_action_keys"] == ["publish-release"]
    assert body["status"] == "approved"
    scheduled.assert_called_once()
    # Out of AWAITING, so the continuation dispatches — not stranded, not DONE.
    assert _stage_status(db_session, gated_ticket) == StageStatus.PENDING
