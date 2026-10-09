from datetime import timedelta

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import Approval, ApprovalKind, ApprovalStatus
from loregarden.models.domain.enums import utcnow
from loregarden.testing.factories import make_ticket, make_workspace
from sqlmodel import Session


@pytest.fixture
def decided_tree():
    """A parent with one child: decisions on both, plus one still pending."""
    from loregarden.db.session import engine

    with Session(engine) as session:
        workspace = make_workspace(session, slug="history-ws")
        parent = make_ticket(session, workspace_id=workspace.id, external_id="hist-parent")
        child = make_ticket(
            session,
            workspace_id=workspace.id,
            external_id="hist-child",
            parent_ticket_id=parent.id,
        )
        now = utcnow()
        rows = [
            Approval(
                ticket_id=parent.id,
                workspace_id=workspace.id,
                kind=ApprovalKind.WORKFLOW_GATE,
                title="Approve Plan completion",
                stage_key="plan",
                status=ApprovalStatus.APPROVED,
                resolved_at=now - timedelta(hours=2),
            ),
            Approval(
                ticket_id=child.id,
                workspace_id=workspace.id,
                kind=ApprovalKind.CLI_PERMISSION,
                title="Allow Bash",
                stage_key="implement",
                status=ApprovalStatus.REJECTED,
                resolved_at=now - timedelta(hours=1),
            ),
            Approval(
                ticket_id=parent.id,
                workspace_id=workspace.id,
                kind=ApprovalKind.WORKFLOW_GATE,
                title="Approve Review completion",
                stage_key="review",
                status=ApprovalStatus.APPROVED,
                resolved_by="automation",
                resolved_at=now,
            ),
            Approval(
                ticket_id=parent.id,
                workspace_id=workspace.id,
                kind=ApprovalKind.WORKFLOW_GATE,
                title="Approve Gate completion",
                stage_key="gate",
                status=ApprovalStatus.PENDING,
            ),
        ]
        session.add_all(rows)
        session.commit()
        return parent.id, child.id


def test_lists_decided_approvals_across_the_subtree_newest_first(
    client: TestClient, decided_tree: tuple[str, str]
):
    parent_id, child_id = decided_tree

    response = client.get(f"/api/inbox/approvals/history?ticket_id={parent_id}")

    assert response.status_code == 200
    body = response.json()
    assert [item["title"] for item in body] == [
        "Approve Review completion",
        "Allow Bash",
        "Approve Plan completion",
    ]
    assert [item["status"] for item in body] == ["approved", "rejected", "approved"]
    assert body[0]["resolved_by"] == "automation"
    assert body[1]["ticket_id"] == child_id
    assert body[1]["ticket_external_id"] == "hist-child"


def test_unknown_ticket_is_a_404_not_an_empty_history(client: TestClient):
    response = client.get("/api/inbox/approvals/history?ticket_id=no-such-ticket")

    assert response.status_code == 404
