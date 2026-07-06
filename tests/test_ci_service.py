"""Unit tests for CI Service."""

import json
import pytest
from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Session, select

from loregarden.models.domain import (
    AutoFixAttempt,
    AutoFixStatus,
    CIRunResult,
    CIStatus,
    Ticket,
    TicketState,
    Workspace,
    WorkItemType,
)
from loregarden.services.ci_service import CIService


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


@pytest.fixture
def ci_service(session: Session) -> CIService:
    """Create CI service."""
    return CIService(session)


class TestCIServiceGitHubActions:
    """Test GitHub Actions webhook processing."""

    def test_process_github_actions_passing(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test processing passing GitHub Actions workflow."""
        payload = {
            "workflow_run": {
                "id": 123456,
                "conclusion": "success",
                "logs_url": "https://github.com/org/repo/runs/123456/logs",
                "head_branch": "feature/ticket-auth-123",
            }
        }

        result = ci_service._process_github_actions(workspace.id, payload)

        assert result is not None
        assert result.status == CIStatus.PASSING
        assert result.ticket_id == ticket.id
        assert result.external_run_id == "123456"
        assert result.provider == "github_actions"

    def test_process_github_actions_failing(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket, session: Session
    ):
        """Test processing failing GitHub Actions workflow."""
        payload = {
            "workflow_run": {
                "id": 123457,
                "conclusion": "failure",
                "logs_url": "https://github.com/org/repo/runs/123457/logs",
                "head_branch": "feature/ticket-auth-123",
            }
        }

        result = ci_service._process_github_actions(workspace.id, payload)

        assert result is not None
        assert result.status == CIStatus.FAILING
        assert result.ticket_id == ticket.id

        # Verify auto-fix attempt was created
        stmt = select(AutoFixAttempt).where(
            AutoFixAttempt.ci_run_result_id == result.id
        )
        attempts = session.exec(stmt).all()
        assert len(attempts) == 1
        assert attempts[0].attempt_number == 1
        assert attempts[0].status == AutoFixStatus.PENDING

    def test_process_github_actions_partial(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test processing partial GitHub Actions workflow."""
        payload = {
            "workflow_run": {
                "id": 123458,
                "conclusion": "neutral",
                "logs_url": "https://github.com/org/repo/runs/123458/logs",
                "head_branch": "feature/ticket-auth-123",
            }
        }

        result = ci_service._process_github_actions(workspace.id, payload)

        assert result is not None
        assert result.status == CIStatus.PARTIAL

    def test_process_github_actions_skipped(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test processing skipped GitHub Actions workflow."""
        payload = {
            "workflow_run": {
                "id": 123459,
                "conclusion": "skipped",
                "logs_url": "https://github.com/org/repo/runs/123459/logs",
                "head_branch": "feature/ticket-auth-123",
            }
        }

        result = ci_service._process_github_actions(workspace.id, payload)

        assert result is not None
        assert result.status == CIStatus.SKIPPED

    def test_ticket_not_found(
        self, ci_service: CIService, workspace: Workspace
    ):
        """Test when ticket ID cannot be extracted."""
        payload = {
            "workflow_run": {
                "id": 123460,
                "conclusion": "failure",
                "logs_url": "https://github.com/org/repo/runs/123460/logs",
                "head_branch": "invalid-branch",
            }
        }

        result = ci_service._process_github_actions(workspace.id, payload)

        assert result is None


class TestExtractTicketIdFromBranch:
    """Test ticket ID extraction from branch names."""

    def test_extract_with_ticket_prefix(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test extracting ticket ID with 'ticket-' prefix."""
        ticket_id = ci_service._extract_ticket_id_from_branch(
            "feature/ticket-auth-123", workspace.id
        )
        assert ticket_id == ticket.id

    def test_extract_from_branch_suffix(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test extracting ticket ID from branch suffix."""
        # Ticket external_id is "auth-123", create another with simpler id
        simpler_ticket = Ticket(
            id=str(uuid4()),
            external_id="auth-system",
            workspace_id=workspace.id,
            title="Auth system",
            state=TicketState.IN_PROGRESS,
            work_item_type=WorkItemType.FEATURE,
        )
        ci_service.session.add(simpler_ticket)
        ci_service.session.commit()

        ticket_id = ci_service._extract_ticket_id_from_branch(
            "feature/auth-system", workspace.id
        )
        assert ticket_id == simpler_ticket.id

    def test_extract_empty_branch(
        self, ci_service: CIService, workspace: Workspace
    ):
        """Test extracting from empty branch."""
        ticket_id = ci_service._extract_ticket_id_from_branch("", workspace.id)
        assert ticket_id is None

    def test_extract_invalid_format(
        self, ci_service: CIService, workspace: Workspace
    ):
        """Test extracting from invalid branch format."""
        ticket_id = ci_service._extract_ticket_id_from_branch("main", workspace.id)
        assert ticket_id is None


class TestAutoFixLogic:
    """Test auto-fix attempt creation and retry logic."""

    def test_trigger_auto_fix_first_attempt(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test triggering first auto-fix attempt."""
        ci_result = CIRunResult(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            status=CIStatus.FAILING,
            provider="github_actions",
            failure_summary="Test failed",
            full_logs="FAILED test_auth.py::test_login",
        )
        ci_service.session.add(ci_result)
        ci_service.session.commit()

        attempt = ci_service.trigger_auto_fix(ci_result, max_attempts=3)

        assert attempt is not None
        assert attempt.attempt_number == 1
        assert attempt.status == AutoFixStatus.PENDING
        assert attempt.ci_run_result_id == ci_result.id

    def test_trigger_auto_fix_respects_max_attempts(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test that auto-fix respects max attempts limit."""
        ci_result = CIRunResult(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            status=CIStatus.FAILING,
            provider="github_actions",
        )
        ci_service.session.add(ci_result)
        ci_service.session.commit()

        # Create max_attempts attempts
        for i in range(3):
            attempt = AutoFixAttempt(
                id=str(uuid4()),
                ci_run_result_id=ci_result.id,
                attempt_number=i + 1,
                status=AutoFixStatus.FAILED,
            )
            ci_service.session.add(attempt)
        ci_service.session.commit()

        # Try to trigger one more
        attempt = ci_service.trigger_auto_fix(ci_result, max_attempts=3)
        assert attempt is None

    def test_get_auto_fix_history(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test retrieving auto-fix history for a ticket."""
        ci_result = CIRunResult(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            status=CIStatus.FAILING,
            provider="github_actions",
        )
        ci_service.session.add(ci_result)
        ci_service.session.commit()

        # Create multiple attempts
        attempt1 = AutoFixAttempt(
            id=str(uuid4()),
            ci_run_result_id=ci_result.id,
            attempt_number=1,
            status=AutoFixStatus.FAILED,
        )
        attempt2 = AutoFixAttempt(
            id=str(uuid4()),
            ci_run_result_id=ci_result.id,
            attempt_number=2,
            status=AutoFixStatus.RUNNING,
        )
        ci_service.session.add_all([attempt1, attempt2])
        ci_service.session.commit()

        history = ci_service.get_auto_fix_history(ticket.id)
        assert len(history) == 2
        assert history[0].attempt_number == 1
        assert history[1].attempt_number == 2


class TestErrorParsing:
    """Test error log extraction and parsing."""

    def test_parse_test_failure(self, ci_service: CIService):
        """Test parsing test failure logs."""
        logs = """
        ===== test session starts =====
        FAILED tests/test_auth.py::TestAuth::test_login
        FAILED tests/test_auth.py::TestAuth::test_logout
        ===== 2 failed in 0.42s =====
        """

        context = ci_service.extract_error_context(logs)

        assert context["error_type"] == "test_failure"
        assert len(context["failing_tests"]) >= 1
        assert "TestAuth::test_login" in context["failing_tests"][0]

    def test_parse_lint_failure(self, ci_service: CIService):
        """Test parsing lint failure logs."""
        logs = """
        ./src/auth.ts
          1:5  error  Unused variable 'x'  no-unused-vars

        1 error found
        """

        context = ci_service.extract_error_context(logs)

        assert context["error_type"] == "lint_error"
        assert "Lint" in context["summary"]

    def test_parse_build_failure(self, ci_service: CIService):
        """Test parsing build failure logs."""
        logs = """
        Compiling...
        error TS2304: Cannot find name 'unknownType'
        Build failed
        """

        context = ci_service.extract_error_context(logs)

        assert context["error_type"] == "build_error"

    def test_parse_empty_logs(self, ci_service: CIService):
        """Test parsing empty logs."""
        context = ci_service.extract_error_context("")

        assert context["error_type"] == "unknown"
        assert context["summary"] == "CI failed (no logs available)"

    def test_parse_unknown_error(self, ci_service: CIService):
        """Test parsing unknown error format."""
        logs = "Something went wrong in some system"

        context = ci_service.extract_error_context(logs)

        assert context["error_type"] == "unknown"


class TestSkipCICheck:
    """Test CI check skip functionality."""

    def test_skip_ci_check(
        self, ci_service: CIService, workspace: Workspace, ticket: Ticket
    ):
        """Test skipping CI check manually."""
        ci_result = CIRunResult(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            status=CIStatus.FAILING,
            provider="github_actions",
        )
        ci_service.session.add(ci_result)
        ci_service.session.commit()

        ci_service.skip_ci_check(ticket.id)

        # Verify status updated
        updated = ci_service.get_latest_ci_status(ticket.id)
        assert updated.status == CIStatus.SKIPPED


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
