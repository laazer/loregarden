"""Tests for inline ticket diff code review comments."""

from fastapi.testclient import TestClient
from loregarden.models.domain import Ticket, Workspace
from sqlmodel import Session


def _workspace(db_session: Session) -> Workspace:
    ws = Workspace(name="Test", slug="test")
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    return ws


def _ticket(db_session: Session, workspace: Workspace) -> Ticket:
    ticket = Ticket(
        external_id="TK-1",
        workspace_id=workspace.id,
        title="Test ticket",
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


class TestTicketDiffComments:
    def test_add_and_list_inline_comment(self, client: TestClient, db_session: Session):
        ws = _workspace(db_session)
        ticket = _ticket(db_session, ws)

        response = client.post(
            f"/api/tickets/{ticket.id}/diff-comments",
            json={
                "file_path": "client/src/App.tsx",
                "line_index": 4,
                "line_kind": "a",
                "content": "Consider extracting this into a hook",
                "created_by": "reviewer@example.com",
            },
        )
        assert response.status_code == 200
        data = response.json()
        assert data["file_path"] == "client/src/App.tsx"
        assert data["line_index"] == 4
        assert data["content"].startswith("Consider extracting")

        listed = client.get(f"/api/tickets/{ticket.id}/diff-comments")
        assert listed.status_code == 200
        payload = listed.json()
        assert payload["total"] == 1
        assert payload["comments"][0]["line_kind"] == "a"

    def test_submit_review_to_agent(self, client: TestClient, db_session: Session, monkeypatch):
        ws = _workspace(db_session)
        ticket = _ticket(db_session, ws)

        client.post(
            f"/api/tickets/{ticket.id}/diff-comments",
            json={
                "file_path": "server/main.py",
                "line_index": 10,
                "line_kind": "d",
                "content": "This delete looks risky",
            },
        )

        monkeypatch.setattr(
            "loregarden.api.diff_review.send_triage_message",
            lambda session, t, content: {"message": content, "ticket_id": t.id},
        )

        submit = client.post(
            f"/api/tickets/{ticket.id}/diff-comments/submit-to-agent",
            json={"instructions": "Please address review comments"},
        )
        assert submit.status_code == 200
        body = submit.json()
        assert body["submitted_comments"] == 1
        assert "Inline code review" in body["message_preview"]

    def test_comment_ticket_not_found(self, client: TestClient):
        response = client.post(
            "/api/tickets/missing/diff-comments",
            json={
                "file_path": "a.ts",
                "line_index": 0,
                "line_kind": "c",
                "content": "nope",
            },
        )
        assert response.status_code == 404
