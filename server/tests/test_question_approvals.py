import json

from fastapi.testclient import TestClient
from sqlmodel import Session, select

from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus


def test_resolve_question_approval_requires_answers(client: TestClient):
    approval = Approval(
        ticket_id=client.get("/api/tickets").json()[0]["id"],
        workspace_id="ws-seed-loregarden",
        kind=ApprovalKind.CLI_QUESTION,
        title="Agent questions",
        stage_key="testing",
        tool_name="AskUserQuestion",
        tool_input_json=json.dumps(
            {
                "questions": [
                    {
                        "question": "Pick a runner?",
                        "options": [{"label": "pytest"}, {"label": "npm test"}],
                    }
                ]
            }
        ),
        status=ApprovalStatus.PENDING,
    )
    # seed workspace id lookup via ticket
    from loregarden.db.session import engine

    with Session(engine) as session:
        from loregarden.models.domain import Ticket

        ticket = session.exec(select(Ticket).limit(1)).first()
        approval.ticket_id = ticket.id
        approval.workspace_id = ticket.workspace_id
        session.add(approval)
        session.commit()
        session.refresh(approval)
        approval_id = approval.id

    missing = client.post(f"/api/inbox/approvals/{approval_id}", json={"action": "approve"})
    assert missing.status_code == 400

    ok = client.post(
        f"/api/inbox/approvals/{approval_id}",
        json={"action": "approve", "answers": {"Pick a runner?": "pytest"}},
    )
    assert ok.status_code == 200
    assert ok.json()["status"] == "approved"

    with Session(engine) as session:
        stored = session.get(Approval, approval_id)
        payload = json.loads(stored.response_json)
        assert payload["updated_input"]["answers"]["Pick a runner?"] == "pytest"
