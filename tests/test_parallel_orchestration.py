"""Unit tests for parallel run orchestration."""

import pytest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch
from uuid import uuid4

from sqlmodel import Session

from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    WorkItemType,
    WorkflowInstance,
    WorkflowTemplate,
)
from loregarden.services.orchestration import OrchestrationService


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
def workflow_template(session: Session, workspace: Workspace) -> WorkflowTemplate:
    """Create test workflow template."""
    template = WorkflowTemplate(
        id=str(uuid4()),
        slug="tdd-template",
        name="TDD Workflow",
        workspace_id=workspace.id,
        stages_json='[{"key":"plan","name":"Plan","agent_id":"planner","order":0}]',
        tags_json='["builtin"]',
    )
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


@pytest.fixture
def ticket(session: Session, workspace: Workspace, workflow_template: WorkflowTemplate) -> Ticket:
    """Create test ticket."""
    ticket = Ticket(
        id=str(uuid4()),
        external_id="feature-123",
        workspace_id=workspace.id,
        title="Test feature",
        state=TicketState.BACKLOG,
        work_item_type=WorkItemType.FEATURE,
        workflow_template_id=workflow_template.id,
    )
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


@pytest.fixture
def orchestration_service(session: Session) -> OrchestrationService:
    """Create OrchestrationService."""
    return OrchestrationService(session)


class TestCreateParallelRun:
    """Test parallel run creation."""

    @pytest.mark.asyncio
    async def test_create_parallel_run_starts_immediately_with_slot(
        self,
        orchestration_service: OrchestrationService,
        ticket: Ticket,
        session: Session,
    ):
        """Test run starts immediately when slot available."""
        with patch.object(
            orchestration_service, "start_run"
        ) as mock_start_run, patch(
            "loregarden.services.orchestration.WorktreeService"
        ) as mock_worktree_class, patch(
            "loregarden.services.orchestration.ParallelQueueService"
        ) as mock_queue_class:

            # Setup mocks
            run = AgentRun(
                id=str(uuid4()),
                run_code="run_test",
                ticket_id=ticket.id,
                workspace_id=ticket.workspace_id,
                agent_id="test-agent",
                status=RunStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
            session.add(run)
            session.commit()

            mock_start_run.return_value = run

            mock_worktree = MagicMock()
            mock_worktree.id = str(uuid4())
            mock_worktree.worktree_path = "/tmp/test-worktree"

            mock_worktree_instance = MagicMock()
            mock_worktree_instance.create_worktree.return_value = mock_worktree
            mock_worktree_instance.get_worktree.return_value = mock_worktree
            mock_worktree_class.return_value = mock_worktree_instance

            mock_queue_instance = AsyncMock()
            mock_queue_instance.get_queue_stats.return_value = {
                "available_slots": 1,
                "active_count": 2,
                "queued_count": 0,
            }
            mock_queue_instance.queue_run.return_value = {
                "status": "started",
                "slot_number": 3,
            }
            mock_queue_class.return_value = mock_queue_instance

            result = await orchestration_service.create_parallel_run(ticket)

            assert result["status"] == "started"
            assert result["run"].id == run.id
            assert result["worktree_id"] == mock_worktree.id

    @pytest.mark.asyncio
    async def test_create_parallel_run_queues_when_no_slots(
        self,
        orchestration_service: OrchestrationService,
        ticket: Ticket,
    ):
        """Test run gets queued when no slots available."""
        with patch(
            "loregarden.services.orchestration.ParallelQueueService"
        ) as mock_queue_class:

            mock_queue_instance = AsyncMock()
            mock_queue_instance.get_queue_stats.return_value = {
                "available_slots": 0,
                "active_count": 3,
                "queued_count": 2,
            }
            mock_queue_instance.queue_run.return_value = {
                "status": "queued",
                "position": 3,
                "queue_length": 3,
                "message": "Added to queue at position 3",
            }
            mock_queue_class.return_value = mock_queue_instance

            result = await orchestration_service.create_parallel_run(ticket)

            assert result["status"] == "queued"
            assert result["position"] == 3
            assert result["queue_length"] == 3


class TestCreateRunInWorktree:
    """Test run creation in worktree."""

    @pytest.mark.asyncio
    async def test_create_run_in_worktree_success(
        self,
        orchestration_service: OrchestrationService,
        ticket: Ticket,
        session: Session,
    ):
        """Test successful run creation in worktree."""
        with patch.object(
            orchestration_service, "start_run"
        ) as mock_start_run, patch(
            "loregarden.services.orchestration.WorktreeService"
        ) as mock_worktree_class:

            run = AgentRun(
                id=str(uuid4()),
                run_code="run_test",
                ticket_id=ticket.id,
                workspace_id=ticket.workspace_id,
                agent_id="test-agent",
                status=RunStatus.RUNNING,
                started_at=datetime.now(timezone.utc),
            )
            session.add(run)
            session.commit()

            mock_start_run.return_value = run

            mock_worktree = MagicMock()
            mock_worktree.id = str(uuid4())
            mock_worktree.worktree_path = "/tmp/test-worktree"

            mock_worktree_instance = MagicMock()
            mock_worktree_instance.create_worktree.return_value = mock_worktree
            mock_worktree_class.return_value = mock_worktree_instance

            mock_queue_service = AsyncMock()
            mock_queue_service.queue_run.return_value = {
                "status": "started",
                "slot_number": 1,
            }

            result = await orchestration_service._create_run_in_worktree(
                ticket=ticket,
                stage_key=None,
                worktree_service=mock_worktree_instance,
                queue_service=mock_queue_service,
            )

            assert result["status"] == "started"
            assert result["run"].id == run.id
            assert result["worktree_id"] == mock_worktree.id


class TestOnParallelRunComplete:
    """Test parallel run completion."""

    @pytest.mark.asyncio
    async def test_on_parallel_run_complete_merges_and_promotes(
        self,
        orchestration_service: OrchestrationService,
        ticket: Ticket,
        session: Session,
    ):
        """Test run completion merges worktree and promotes from queue."""
        run = AgentRun(
            id=str(uuid4()),
            run_code="run_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            worktree_id=str(uuid4()),
            agent_id="test-agent",
            status=RunStatus.SUCCEEDED,
            finished_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()

        with patch(
            "loregarden.services.orchestration.WorktreeService"
        ) as mock_worktree_class, patch(
            "loregarden.services.orchestration.ParallelQueueService"
        ) as mock_queue_class:

            mock_worktree = MagicMock()
            mock_worktree.id = run.worktree_id
            mock_worktree.conflict_files = []

            mock_worktree_instance = MagicMock()
            mock_worktree_instance.get_worktree.return_value = mock_worktree
            mock_worktree_instance.merge_worktree.return_value = True
            mock_worktree_class.return_value = mock_worktree_instance

            mock_queue_instance = AsyncMock()
            mock_queue_instance.on_run_complete.return_value = {
                "status": "promoted",
                "next_run": {"run_id": str(uuid4()), "ticket_id": str(uuid4())},
                "message": "Promoted next run",
            }
            mock_queue_class.return_value = mock_queue_instance

            result = await orchestration_service.on_parallel_run_complete(run)

            assert result["status"] == "merged"
            assert "promoted" in result.get("message", "").lower() or result.get("next_run")

    @pytest.mark.asyncio
    async def test_on_parallel_run_complete_conflicts(
        self,
        orchestration_service: OrchestrationService,
        ticket: Ticket,
        session: Session,
    ):
        """Test run completion with merge conflicts."""
        run = AgentRun(
            id=str(uuid4()),
            run_code="run_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            worktree_id=str(uuid4()),
            agent_id="test-agent",
            status=RunStatus.SUCCEEDED,
            finished_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()

        with patch(
            "loregarden.services.orchestration.WorktreeService"
        ) as mock_worktree_class:

            mock_worktree = MagicMock()
            mock_worktree.id = run.worktree_id
            mock_worktree.conflict_files = ["file1.py", "file2.py"]

            mock_worktree_instance = MagicMock()
            mock_worktree_instance.get_worktree.return_value = mock_worktree
            mock_worktree_instance.merge_worktree.return_value = False
            mock_worktree_class.return_value = mock_worktree_instance

            result = await orchestration_service.on_parallel_run_complete(run)

            assert result["status"] == "conflicts"
            assert result["conflict_files"] == ["file1.py", "file2.py"]

    @pytest.mark.asyncio
    async def test_on_parallel_run_complete_no_worktree(
        self,
        orchestration_service: OrchestrationService,
        ticket: Ticket,
        session: Session,
    ):
        """Test run completion without worktree (non-parallel run)."""
        run = AgentRun(
            id=str(uuid4()),
            run_code="run_test",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id="test-agent",
            status=RunStatus.SUCCEEDED,
            finished_at=datetime.now(timezone.utc),
        )
        session.add(run)
        session.commit()

        with patch(
            "loregarden.services.orchestration.ParallelQueueService"
        ) as mock_queue_class:

            mock_queue_instance = AsyncMock()
            mock_queue_instance.on_run_complete.return_value = {
                "status": "slot_freed",
                "message": "Slot freed, no runs in queue",
            }
            mock_queue_class.return_value = mock_queue_instance

            result = await orchestration_service.on_parallel_run_complete(run)

            assert result["status"] == "merged"


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
