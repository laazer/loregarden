import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from loregarden.agents.executors.permission_bridge import parse_stored_tool_input, serialize_tool_input
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus, Ticket


def test_serialize_tool_input_preserves_large_write_payload():
    payload = {"file_path": "a.md", "content": "x" * 5000}
    stored = serialize_tool_input(payload)
    assert len(stored) > 4000
    assert json.loads(stored) == payload


def test_parse_stored_tool_input_tolerates_truncated_legacy_json():
    broken = '{"file_path": "/tmp/x", "content": "' + ("a" * 200)
    assert parse_stored_tool_input(broken) == {}


def test_resolve_cli_permission_with_truncated_tool_input_json(client: TestClient):
    from loregarden.db.session import engine

    with Session(engine) as session:
        ticket = session.exec(select(Ticket).limit(1)).first()
        assert ticket is not None
        approval = Approval(
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            kind=ApprovalKind.CLI_PERMISSION,
            title="Allow Write?",
            stage_key="testing",
            tool_name="Write",
            tool_input_json='{"file_path": "/tmp/x", "content": "' + ("a" * 200),
            status=ApprovalStatus.PENDING,
        )
        session.add(approval)
        session.commit()
        session.refresh(approval)
        approval_id = approval.id

    response = client.post(f"/api/inbox/approvals/{approval_id}", json={"action": "approve"})
    assert response.status_code == 200
    assert response.json()["status"] == "approved"

    with Session(engine) as session:
        stored = session.get(Approval, approval_id)
        assert stored is not None
        assert stored.status == ApprovalStatus.APPROVED
        payload = json.loads(stored.response_json)
        assert payload["updated_input"] == {}
