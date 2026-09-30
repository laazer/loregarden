"""HTTP regressions for exit-action inbox resolution (AC-8/AC-9/AC-12).

Bare ``action=recheck`` must not 500 on an empty client body, and approve of a
grantable-authority gate must schedule a continuation rather than mark the
stage done.
"""

from __future__ import annotations

import json
from unittest.mock import patch

from fastapi.testclient import TestClient
from loregarden.models.domain import (
    Approval,
    ApprovalKind,
    ApprovalStatus,
    Ticket,
)
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


def test_inbox_bare_recheck_probes_server_side_snapshot(client: TestClient, db_session: Session):
    """AC-8/AC-12: POST action=recheck with no body.answers must not 500."""
    approval = _seed_recheck_gate(db_session)

    with (
        patch(
            "loregarden.services.exit_action_approvals.probe_credentials",
            return_value={"claude_profile": "available"},
        ),
        patch(
            "loregarden.services.exit_action_approvals.probe_capabilities",
            return_value={},
        ),
        patch(
            "loregarden.services.exit_action_approvals.probe_authority",
            return_value={},
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
    assert body["status"] == "pending"
    assert body["newly_assigned_action_keys"] == ["read-usage"]
    scheduled.assert_called_once()
    assert scheduled.call_args.kwargs["assigned_exit_action_keys"] == ["read-usage"]


def test_inbox_recheck_ignores_client_answers(client: TestClient, db_session: Session):
    """Client answers must not become the runtime snapshot used for recheck."""
    approval = _seed_recheck_gate(db_session)

    with (
        patch(
            "loregarden.services.exit_action_approvals.probe_credentials",
            return_value={"claude_profile": "unavailable"},
        ),
        patch(
            "loregarden.services.exit_action_approvals.probe_capabilities",
            return_value={},
        ),
        patch(
            "loregarden.services.exit_action_approvals.probe_authority",
            return_value={},
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
    assert body["newly_assigned_action_keys"] == []
    scheduled.assert_not_called()
    db_session.refresh(approval)
    assert approval.status == ApprovalStatus.PENDING
    remaining = json.loads(approval.tool_input_json)["human_required_actions"]
    assert remaining[0]["action_key"] == "read-usage"


def test_inbox_approve_authority_schedules_continuation(client: TestClient, db_session: Session):
    """AC-9: approve of grantable authority must not mark the stage done."""
    approval = _seed_authority_gate(db_session)
    ticket = db_session.get(Ticket, approval.ticket_id)
    assert ticket is not None
    stage_status_before = ticket.workflow_stage_status.value

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
    assert scheduled.call_args.kwargs["assigned_exit_action_keys"] == ["publish-release"]

    db_session.refresh(ticket)
    assert ticket.workflow_stage_status.value == stage_status_before
