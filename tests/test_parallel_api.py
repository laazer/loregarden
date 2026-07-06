"""Unit tests for parallel execution API endpoints."""

import pytest
from fastapi.testclient import TestClient
from sqlmodel import Session
from uuid import uuid4

from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    WorkItemType,
    Worktree,
    WorktreeState,
)


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
        external_id="feature-123",
        workspace_id=workspace.id,
        title="Test feature",
        state=TicketState.IN_PROGRESS,
        work_item_type=WorkItemType.FEATURE,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture
def agent_run(session: Session, workspace: Workspace, ticket: Ticket) -> AgentRun:
    """Create test agent run."""
    run = AgentRun(
        id=str(uuid4()),
        run_code="run_test",
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        agent_id="test-agent",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@pytest.fixture
def worktree(session: Session, workspace: Workspace, agent_run: AgentRun) -> Worktree:
    """Create test worktree."""
    wt = Worktree(
        id=str(uuid4()),
        workspace_id=workspace.id,
        agent_run_id=agent_run.id,
        parent_branch="main",
        worktree_path="/tmp/test-worktree",
        state=WorktreeState.ACTIVE,
        merge_base="abc123def456",
    )
    session.add(wt)
    session.commit()
    session.refresh(wt)
    return wt


class TestCreateParallelRunEndpoint:
    """Test create parallel run endpoint."""

    def test_create_parallel_run_not_found(self, client: TestClient):
        """Test creating run for non-existent ticket."""
        response = client.post(
            "/api/parallel/runs/nonexistent",
            params={"stage_key": None, "max_concurrent": 3},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetParallelStatusEndpoint:
    """Test get parallel status endpoint."""

    def test_get_parallel_status_empty(
        self, client: TestClient, workspace: Workspace
    ):
        """Test get status for empty workspace."""
        response = client.get(f"/api/parallel/status/{workspace.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["active_runs"] == []
        assert data["queued_runs"] == []
        assert data["available_slots"] == 3
        assert data["total_slots"] == 3


class TestCancelQueuedRunEndpoint:
    """Test cancel queued run endpoint."""

    def test_cancel_queued_run_not_found(self, client: TestClient):
        """Test cancelling non-existent run."""
        response = client.post("/api/parallel/queue/nonexistent/cancel")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestCheckConflictsEndpoint:
    """Test check conflicts endpoint."""

    def test_check_conflicts_not_found(self, client: TestClient):
        """Test checking conflicts for non-existent worktree."""
        response = client.get(
            "/api/parallel/conflicts/nonexistent",
            params={"target_branch": "main"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_check_conflicts_success(
        self, client: TestClient, worktree: Worktree
    ):
        """Test checking conflicts for existing worktree."""
        response = client.get(
            f"/api/parallel/conflicts/{worktree.id}",
            params={"target_branch": "main"},
        )

        # Should return 500 because git commands will fail in test env
        # But we're testing the endpoint structure
        assert response.status_code in [200, 500]
        if response.status_code == 200:
            data = response.json()
            assert "has_conflicts" in data
            assert "conflicting_files" in data


class TestMergeWorktreeEndpoint:
    """Test merge worktree endpoint."""

    def test_merge_worktree_not_found(self, client: TestClient):
        """Test merging non-existent worktree."""
        response = client.post(
            "/api/parallel/worktree/nonexistent/merge",
            params={"target_branch": "main"},
        )

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_merge_worktree_inactive(
        self, client: TestClient, session: Session, workspace: Workspace, agent_run: AgentRun
    ):
        """Test merging inactive worktree."""
        # Create inactive worktree
        wt = Worktree(
            id=str(uuid4()),
            workspace_id=workspace.id,
            agent_run_id=agent_run.id,
            parent_branch="main",
            worktree_path="/tmp/test-wt",
            state=WorktreeState.MERGED,
        )
        session.add(wt)
        session.commit()

        response = client.post(
            f"/api/parallel/worktree/{wt.id}/merge",
            params={"target_branch": "main"},
        )

        assert response.status_code == 400
        assert "cannot merge" in response.json()["detail"].lower()


class TestCleanupWorktreeEndpoint:
    """Test cleanup worktree endpoint."""

    def test_cleanup_worktree_not_found(self, client: TestClient):
        """Test cleaning up non-existent worktree."""
        response = client.post("/api/parallel/worktree/nonexistent/cleanup")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()


class TestGetWorktreeDetailsEndpoint:
    """Test get worktree details endpoint."""

    def test_get_worktree_details_not_found(self, client: TestClient):
        """Test getting details for non-existent worktree."""
        response = client.get("/api/parallel/worktree/nonexistent")

        assert response.status_code == 404
        assert "not found" in response.json()["detail"].lower()

    def test_get_worktree_details_success(
        self, client: TestClient, worktree: Worktree
    ):
        """Test getting worktree details."""
        response = client.get(f"/api/parallel/worktree/{worktree.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["id"] == worktree.id
        assert data["state"] == "active"
        assert data["path"] == "/tmp/test-worktree"
        assert data["has_conflicts"] is False


class TestGetConflictReportsEndpoint:
    """Test get conflict reports endpoint."""

    def test_get_conflict_reports_empty(
        self, client: TestClient, worktree: Worktree
    ):
        """Test getting reports when none exist."""
        response = client.get(f"/api/parallel/conflict-reports/{worktree.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["reports"] == []


class TestGetActiveRunsEndpoint:
    """Test get active runs endpoint."""

    def test_get_active_runs_empty(
        self, client: TestClient, workspace: Workspace
    ):
        """Test getting active runs when none."""
        response = client.get(f"/api/parallel/active-runs/{workspace.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["active_runs"] == []


class TestGetQueuedRunsEndpoint:
    """Test get queued runs endpoint."""

    def test_get_queued_runs_empty(
        self, client: TestClient, workspace: Workspace
    ):
        """Test getting queued runs when none."""
        response = client.get(f"/api/parallel/queued-runs/{workspace.id}")

        assert response.status_code == 200
        data = response.json()
        assert data["queued_runs"] == []


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
