"""Unit tests for CI API endpoints."""

import json
import pytest
import hmac
import hashlib
from uuid import uuid4

from fastapi.testclient import TestClient
from sqlmodel import Session

from loregarden.models.domain import (
    CIRunResult,
    CIStatus,
    Ticket,
    TicketState,
    Workspace,
    WorkItemType,
)
from loregarden.config import settings


@pytest.fixture
def workspace(session: Session) -> Workspace:
    """Create test workspace."""
    ws = Workspace(
        id=str(uuid4()),
        slug="test-workspace",
        name="Test Workspace",
    )
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture
def ticket(session: Session, workspace: Workspace) -> Ticket:
    """Create test ticket."""
    ticket = Ticket(
        id=str(uuid4()),
        external_id="auth-123",
        workspace_id=workspace.id,
        title="Add auth system",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


class TestCIWebhookEndpoint:
    """Test CI webhook endpoint."""

    def test_webhook_github_actions_passing(
        self, client: TestClient, workspace: Workspace, ticket: Ticket
    ):
        """Test receiving GitHub Actions passing workflow."""
        payload = {
            "workflow_run": {
                "id": 123456,
                "conclusion": "success",
                "logs_url": "https://github.com/org/repo/runs/123456/logs",
                "head_branch": "feature/ticket-auth-123",
            }
        }

        response = client.post(
            f"/api/ci/webhook/{workspace.id}",
            json=payload,
            headers={
                "X-GitHub-Event": "workflow_run",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["ci_status"] == "passing"

    def test_webhook_github_actions_failing(
        self, client: TestClient, workspace: Workspace, ticket: Ticket
    ):
        """Test receiving GitHub Actions failing workflow."""
        payload = {
            "workflow_run": {
                "id": 123457,
                "conclusion": "failure",
                "logs_url": "https://github.com/org/repo/runs/123457/logs",
                "head_branch": "feature/ticket-auth-123",
            }
        }

        response = client.post(
            f"/api/ci/webhook/{workspace.id}",
            json=payload,
            headers={
                "X-GitHub-Event": "workflow_run",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["ci_status"] == "failing"

    def test_webhook_invalid_event_type(
        self, client: TestClient, workspace: Workspace
    ):
        """Test that non-workflow_run events are ignored."""
        payload = {"action": "opened", "pull_request": {}}

        response = client.post(
            f"/api/ci/webhook/{workspace.id}",
            json=payload,
            headers={
                "X-GitHub-Event": "pull_request",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ignored"

    def test_webhook_invalid_json(
        self, client: TestClient, workspace: Workspace
    ):
        """Test that invalid JSON is rejected."""
        response = client.post(
            f"/api/ci/webhook/{workspace.id}",
            data="invalid json",
            headers={
                "X-GitHub-Event": "workflow_run",
                "Content-Type": "application/json",
            },
        )

        assert response.status_code == 400
        data = response.json()
        assert "JSON" in data["detail"]

    def test_webhook_signature_verification_valid(
        self, client: TestClient, workspace: Workspace, ticket: Ticket
    ):
        """Test GitHub webhook signature verification with valid signature."""
        # Set webhook secret in config
        original_secret = settings.LOREGARDEN_CI_WEBHOOK_SECRET
        settings.LOREGARDEN_CI_WEBHOOK_SECRET = "test-secret-123"

        try:
            payload = {
                "workflow_run": {
                    "id": 123458,
                    "conclusion": "success",
                    "logs_url": "https://github.com/org/repo/runs/123458/logs",
                    "head_branch": "feature/ticket-auth-123",
                }
            }

            payload_bytes = json.dumps(payload).encode()
            signature = hmac.new(
                b"test-secret-123",
                payload_bytes,
                hashlib.sha256,
            ).hexdigest()

            response = client.post(
                f"/api/ci/webhook/{workspace.id}",
                json=payload,
                headers={
                    "X-GitHub-Event": "workflow_run",
                    "X-Hub-Signature-256": f"sha256={signature}",
                },
            )

            assert response.status_code == 200
            data = response.json()
            assert data["status"] == "ok"

        finally:
            settings.LOREGARDEN_CI_WEBHOOK_SECRET = original_secret

    def test_webhook_signature_verification_invalid(
        self, client: TestClient, workspace: Workspace
    ):
        """Test GitHub webhook signature verification with invalid signature."""
        # Set webhook secret in config
        original_secret = settings.LOREGARDEN_CI_WEBHOOK_SECRET
        settings.LOREGARDEN_CI_WEBHOOK_SECRET = "test-secret-123"

        try:
            payload = {
                "workflow_run": {
                    "id": 123459,
                    "conclusion": "success",
                    "logs_url": "https://github.com/org/repo/runs/123459/logs",
                    "head_branch": "feature/ticket-auth-123",
                }
            }

            response = client.post(
                f"/api/ci/webhook/{workspace.id}",
                json=payload,
                headers={
                    "X-GitHub-Event": "workflow_run",
                    "X-Hub-Signature-256": "sha256=invalid-signature",
                },
            )

            assert response.status_code == 403
            data = response.json()
            assert "signature" in data["detail"].lower()

        finally:
            settings.LOREGARDEN_CI_WEBHOOK_SECRET = original_secret


class TestCIStatusEndpoint:
    """Test CI status retrieval endpoint."""

    def test_get_ci_status(
        self, client: TestClient, session: Session, workspace: Workspace, ticket: Ticket
    ):
        """Test fetching CI status for a ticket."""
        # Create CI result
        ci_result = CIRunResult(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            status=CIStatus.PASSING,
            provider="github_actions",
            logs_url="https://example.com/logs",
        )
        session.add(ci_result)
        session.commit()

        response = client.get(f"/api/ci/status/{ticket.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["ci_status"] is not None
        assert data["ci_status"]["status"] == "passing"
        assert data["auto_fix_history"] == []

    def test_get_ci_status_not_found(self, client: TestClient, ticket: Ticket):
        """Test fetching CI status for ticket with no CI results."""
        response = client.get(f"/api/ci/status/{ticket.id}")

        # Should return null status (not an error)
        assert response.status_code == 200
        data = response.json()
        assert data["ci_status"] is None
        assert data["auto_fix_history"] == []


class TestManualOverrideEndpoint:
    """Test manual CI check skip endpoint."""

    def test_skip_ci_check(
        self, client: TestClient, session: Session, workspace: Workspace, ticket: Ticket
    ):
        """Test skipping CI check manually."""
        # Create failing CI result
        ci_result = CIRunResult(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            status=CIStatus.FAILING,
            provider="github_actions",
        )
        session.add(ci_result)
        session.commit()

        response = client.post(f"/api/ci/manual-override/{ticket.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"

        # Verify status changed
        response = client.get(f"/api/ci/status/{ticket.id}")
        updated = response.json()["ci_status"]
        assert updated["status"] == "skipped"

    def test_skip_ci_check_no_result(self, client: TestClient, ticket: Ticket):
        """Test skipping CI check when no CI result exists."""
        response = client.post(f"/api/ci/manual-override/{ticket.id}")

        # Should still succeed (no-op)
        assert response.status_code == 200


class TestTriggerAutoFixEndpoint:
    """Test manual auto-fix trigger endpoint."""

    def test_trigger_auto_fix(
        self, client: TestClient, session: Session, workspace: Workspace, ticket: Ticket
    ):
        """Test manually triggering auto-fix."""
        # Create failing CI result
        ci_result = CIRunResult(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            status=CIStatus.FAILING,
            provider="github_actions",
        )
        session.add(ci_result)
        session.commit()

        response = client.post(f"/api/ci/trigger-auto-fix/{ticket.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["status"] == "ok"
        assert data["attempt_number"] == 1
        assert "triggered" in data["message"].lower()

    def test_trigger_auto_fix_max_attempts(
        self, client: TestClient, session: Session, workspace: Workspace, ticket: Ticket
    ):
        """Test that auto-fix respects max attempts."""
        # Create failing CI result
        ci_result = CIRunResult(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            status=CIStatus.FAILING,
            provider="github_actions",
        )
        session.add(ci_result)
        session.commit()

        # Trigger max times
        from loregarden.models.domain import AutoFixAttempt, AutoFixStatus
        for i in range(3):
            attempt = AutoFixAttempt(
                id=str(uuid4()),
                ci_run_result_id=ci_result.id,
                attempt_number=i + 1,
                status=AutoFixStatus.FAILED,
            )
            session.add(attempt)
        session.commit()

        # Try one more
        response = client.post(f"/api/ci/trigger-auto-fix/{ticket.id}")

        assert response.status_code == 400
        data = response.json()
        assert "Max" in data["detail"] or "max" in data["detail"]

    def test_trigger_auto_fix_no_ci_result(self, client: TestClient, ticket: Ticket):
        """Test triggering auto-fix when no CI result exists."""
        response = client.post(f"/api/ci/trigger-auto-fix/{ticket.id}")

        assert response.status_code == 404
        data = response.json()
        assert "not found" in data["detail"].lower()


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
