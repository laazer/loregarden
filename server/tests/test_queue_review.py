"""Tests for queue operation review system (diffs, comments, approvals)."""

import pytest
from fastapi.testclient import TestClient
from loregarden.models.domain import (
    Workspace,
)
from sqlmodel import Session


@pytest.fixture
def workspace(db_session: Session):
    """Create a test workspace."""
    ws = Workspace(name="test-workspace", slug="test-workspace")
    db_session.add(ws)
    db_session.commit()
    db_session.refresh(ws)
    return ws


class TestDiffGeneration:
    """Test diff generation between queue states."""

    def test_generate_diff_added_runs(self, client: TestClient, workspace):
        """Test detecting added runs."""
        before = [
            {"run_id": "run1", "ticket_id": "TK-1", "position": 0},
        ]
        after = [
            {"run_id": "run1", "ticket_id": "TK-1", "position": 0},
            {"run_id": "run2", "ticket_id": "TK-2", "position": 1},
        ]

        response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
                "description": "Added one run",
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["affected_count"] == 1

        changes = data["changes"]
        assert len(changes) == 1
        assert changes[0]["type"] == "added"
        assert changes[0]["run_id"] == "run2"

    def test_generate_diff_removed_runs(self, client: TestClient, workspace):
        """Test detecting removed runs."""
        before = [
            {"run_id": "run1", "ticket_id": "TK-1"},
            {"run_id": "run2", "ticket_id": "TK-2"},
        ]
        after = [
            {"run_id": "run1", "ticket_id": "TK-1"},
        ]

        response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
                "description": "Removed one run",
            },
        )

        assert response.status_code == 200
        data = response.json()

        changes = data["changes"]
        assert len(changes) == 1
        assert changes[0]["type"] == "removed"
        assert changes[0]["run_id"] == "run2"

    def test_generate_diff_modified_runs(self, client: TestClient, workspace):
        """Test detecting modified runs with field changes."""
        before = [
            {
                "run_id": "run1",
                "ticket_id": "TK-1",
                "position": 0,
                "status": "queued",
            },
        ]
        after = [
            {
                "run_id": "run1",
                "ticket_id": "TK-1",
                "position": 2,
                "status": "running",
            },
        ]

        response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
                "description": "Modified run state",
            },
        )

        assert response.status_code == 200
        data = response.json()

        changes = data["changes"]
        assert len(changes) == 1
        assert changes[0]["type"] == "modified"
        assert changes[0]["run_id"] == "run1"
        assert set(changes[0]["fields_changed"]) == {"position", "status"}

    def test_generate_diff_multiple_changes(self, client: TestClient, workspace):
        """Test diff with mixed additions, removals, and modifications."""
        before = [
            {"run_id": "run1", "ticket_id": "TK-1", "status": "queued"},
            {"run_id": "run2", "ticket_id": "TK-2", "status": "queued"},
            {"run_id": "run3", "ticket_id": "TK-3", "status": "queued"},
        ]
        after = [
            {"run_id": "run1", "ticket_id": "TK-1", "status": "running"},
            {"run_id": "run2", "ticket_id": "TK-2", "status": "queued"},
            {"run_id": "run4", "ticket_id": "TK-4", "status": "queued"},
        ]

        response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
                "description": "Mixed changes",
            },
        )

        assert response.status_code == 200
        data = response.json()
        changes = data["changes"]

        by_type = {}
        for c in changes:
            by_type.setdefault(c["type"], []).append(c["run_id"])

        assert by_type["modified"] == ["run1"]
        assert by_type["removed"] == ["run3"]
        assert by_type["added"] == ["run4"]


class TestOperationComments:
    """Test operation commenting (GitHub-style)."""

    def test_add_general_comment(self, client: TestClient, workspace):
        """Test adding a general comment to an operation."""
        before, after = [], [{"run_id": "run1", "ticket_id": "TK-1"}]
        op_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
            },
        )
        operation_id = op_response.json()["operation_id"]

        comment_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/comment",
            json={
                "content": "This looks good to me",
                "created_by": "reviewer@example.com",
            },
        )

        assert comment_response.status_code == 200
        data = comment_response.json()
        assert data["content"] == "This looks good to me"
        assert data["run_id"] is None
        assert data["line_number"] is None

    def test_add_per_run_comment(self, client: TestClient, workspace):
        """Test adding a comment tied to a specific run."""
        before, after = [], [{"run_id": "run1", "ticket_id": "TK-1"}]
        op_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
            },
        )
        operation_id = op_response.json()["operation_id"]

        comment_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/comment",
            json={
                "content": "Problem with this run",
                "run_id": "run1",
                "created_by": "reviewer@example.com",
            },
        )

        assert comment_response.status_code == 200
        data = comment_response.json()
        assert data["run_id"] == "run1"

    def test_get_operation_with_comments(self, client: TestClient, workspace):
        """Test retrieving operation with comments."""
        before, after = [], [{"run_id": "run1", "ticket_id": "TK-1"}]
        op_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
            },
        )
        operation_id = op_response.json()["operation_id"]

        # Add comments
        client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/comment",
            json={"content": "General comment", "created_by": "user1"},
        )
        client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/comment",
            json={
                "content": "Run-specific comment",
                "run_id": "run1",
                "created_by": "user2",
            },
        )

        # Retrieve operation
        response = client.get(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/diff"
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["comments"]) == 2

        general = [c for c in data["comments"] if not c["run_id"]]
        per_run = [c for c in data["comments"] if c["run_id"]]

        assert len(general) == 1
        assert len(per_run) == 1


class TestOperationApproval:
    """Test operation approval workflow."""

    def test_approve_operation(self, client: TestClient, workspace):
        """Test approving an operation."""
        before, after = [], [{"run_id": "run1", "ticket_id": "TK-1"}]
        op_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
            },
        )
        operation_id = op_response.json()["operation_id"]

        approve_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/approve",
            json={"approved_by": "reviewer@example.com"},
        )

        assert approve_response.status_code == 200
        data = approve_response.json()
        assert data["approved"] is True
        assert data["approved_by"] == "reviewer@example.com"

        # Verify approval persisted
        diff_response = client.get(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/diff"
        )
        assert diff_response.json()["approved"] is True


class TestAgentSubmission:
    """Test submitting operations to agents with review context."""

    def test_submit_to_agent(self, client: TestClient, workspace):
        """Test submitting operation to agent."""
        before = [{"run_id": "old1", "ticket_id": "TK-0"}]
        after = [
            {"run_id": "run1", "ticket_id": "TK-1"},
            {"run_id": "run2", "ticket_id": "TK-2"},
        ]
        op_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
                "description": "Reordered queue",
            },
        )
        operation_id = op_response.json()["operation_id"]

        # Add comment for context
        client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/comment",
            json={
                "content": "Please execute this carefully",
                "created_by": "reviewer",
            },
        )

        # Submit to agent
        submit_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/submit-to-agent",
            json={
                "agent_id": "orchestrator-v1",
                "instructions": "Verify each run before execution",
                "approved_by": "reviewer",
            },
        )

        assert submit_response.status_code == 200
        data = submit_response.json()
        assert data["submitted_to_agent"] == "orchestrator-v1"

        review_context = data["review_context"]
        assert review_context["operation_type"] == "bulk_reorder"
        assert review_context["custom_instructions"] == "Verify each run before execution"
        assert len(review_context["comments"]) == 1
        assert len(review_context["diff"]) == 3  # 1 removed + 2 added


class TestOperationListing:
    """Test operation listing and filtering."""

    def test_list_operations(self, client: TestClient, workspace):
        """Test listing operations."""
        before, after = [], [{"run_id": "run1", "ticket_id": "TK-1"}]

        # Create multiple operations
        for i in range(3):
            client.post(
                f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
                json={
                    "operation_type": "bulk_reorder",
                    "before_state": before,
                    "after_state": after,
                },
            )

        response = client.get(f"/api/parallel/workspace/{workspace.id}/queue/operations")

        assert response.status_code == 200
        data = response.json()
        assert data["total"] >= 3

    def test_list_approved_operations(self, client: TestClient, workspace):
        """Test filtering for approved operations."""
        before, after = [], [{"run_id": "run1", "ticket_id": "TK-1"}]

        # Create operation
        op_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/create",
            json={
                "operation_type": "bulk_reorder",
                "before_state": before,
                "after_state": after,
            },
        )
        operation_id = op_response.json()["operation_id"]

        # Approve it
        client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/{operation_id}/approve"
        )

        # List approved
        response = client.get(
            f"/api/parallel/workspace/{workspace.id}/queue/operations?approved_only=true"
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["operations"]) >= 1
        assert all(op["approved"] for op in data["operations"])


class TestRunOutputReview:
    """Test line-by-line output review."""

    def test_create_output_review(self, client: TestClient, workspace):
        """Test creating an output review."""
        output_content = "Line 1\nLine 2\nLine 3"

        response = client.post(
            f"/api/parallel/workspace/{workspace.id}/runs/run1/output-review",
            json={
                "output_type": "stdout",
                "output_content": output_content,
            },
        )

        assert response.status_code == 200
        data = response.json()
        assert data["run_id"] == "run1"
        assert data["output_type"] == "stdout"
        assert data["line_count"] == 3

    def test_add_line_comment(self, client: TestClient, workspace):
        """Test adding comment to specific output line."""
        output_content = "Error on line 2"

        review_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/runs/run1/output-review",
            json={"output_type": "stderr", "output_content": output_content},
        )
        review_id = review_response.json()["review_id"]

        comment_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/runs/run1/output-review/{review_id}/comment",
            json={
                "line_number": 1,
                "content": "This error needs investigation",
            },
        )

        assert comment_response.status_code == 200
        data = comment_response.json()
        assert data["line_number"] == 1
        assert data["total_comments"] == 1

    def test_get_output_review_with_comments(self, client: TestClient, workspace):
        """Test retrieving output review with line comments."""
        output_content = "Line 1\nLine 2\nLine 3"

        review_response = client.post(
            f"/api/parallel/workspace/{workspace.id}/runs/run1/output-review",
            json={"output_type": "stdout", "output_content": output_content},
        )
        review_id = review_response.json()["review_id"]

        # Add comments to multiple lines
        client.post(
            f"/api/parallel/workspace/{workspace.id}/runs/run1/output-review/{review_id}/comment",
            json={"line_number": 1, "content": "Comment on line 1"},
        )
        client.post(
            f"/api/parallel/workspace/{workspace.id}/runs/run1/output-review/{review_id}/comment",
            json={"line_number": 2, "content": "Comment on line 2"},
        )

        # Retrieve review
        response = client.get(
            f"/api/parallel/workspace/{workspace.id}/runs/run1/output-review/{review_id}"
        )

        assert response.status_code == 200
        data = response.json()
        assert len(data["lines"]) == 3
        assert data["total_comments"] == 2

        # Check comments are grouped by line
        line_1 = data["lines"][0]
        assert len(line_1["comments"]) == 1
        assert line_1["comments"][0]["content"] == "Comment on line 1"

        line_2 = data["lines"][1]
        assert len(line_2["comments"]) == 1


class TestErrorHandling:
    """Test error handling in review system."""

    def test_get_nonexistent_operation(self, client: TestClient, workspace):
        """Test getting non-existent operation returns 404."""
        response = client.get(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/nonexistent/diff"
        )

        assert response.status_code == 404

    def test_add_comment_to_nonexistent_operation(self, client: TestClient, workspace):
        """Test adding comment to non-existent operation returns 404."""
        response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/nonexistent/comment",
            json={"content": "test"},
        )

        assert response.status_code == 404

    def test_approve_nonexistent_operation(self, client: TestClient, workspace):
        """Test approving non-existent operation returns 404."""
        response = client.post(
            f"/api/parallel/workspace/{workspace.id}/queue/operations/nonexistent/approve"
        )

        assert response.status_code == 404
