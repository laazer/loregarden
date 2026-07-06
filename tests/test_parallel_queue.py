"""Unit tests for ParallelQueueService."""

import pytest
from datetime import datetime, timezone
from uuid import uuid4

from sqlmodel import Session

from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
    WorkItemType,
)
from loregarden.services.parallel_queue import ParallelQueueService


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
def queue_service(session: Session) -> ParallelQueueService:
    """Create ParallelQueueService with 3 slots."""
    return ParallelQueueService(session, max_concurrent=3)


class TestInitializeSlots:
    """Test slot initialization."""

    def test_initialize_slots_success(
        self, queue_service: ParallelQueueService, workspace: Workspace, session: Session
    ):
        """Test successful slot initialization."""
        queue_service.initialize_slots(workspace.id)

        # Verify slots created
        from sqlmodel import select
        stmt = select(AgentSlot).where(AgentSlot.workspace_id == workspace.id)
        slots = session.exec(stmt).all()

        assert len(slots) == 3
        assert all(slot.is_available for slot in slots)
        assert [slot.slot_number for slot in slots] == [1, 2, 3]

    def test_initialize_slots_idempotent(
        self, queue_service: ParallelQueueService, workspace: Workspace, session: Session
    ):
        """Test that initialize_slots is idempotent."""
        queue_service.initialize_slots(workspace.id)
        queue_service.initialize_slots(workspace.id)

        # Should still have 3 slots
        from sqlmodel import select
        stmt = select(AgentSlot).where(AgentSlot.workspace_id == workspace.id)
        slots = session.exec(stmt).all()

        assert len(slots) == 3


class TestQueueRun:
    """Test queuing runs."""

    @pytest.mark.asyncio
    async def test_queue_run_starts_immediately_with_available_slot(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
        ticket: Ticket,
        agent_run: AgentRun,
    ):
        """Test run starts immediately when slot is available."""
        result = await queue_service.queue_run(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_id=agent_run.id,
        )

        assert result["status"] == "started"
        assert result["slot_number"] == 1
        assert "immediately" in result["message"].lower()

    @pytest.mark.asyncio
    async def test_queue_run_queues_when_no_slots(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
        session: Session,
    ):
        """Test run gets queued when no slots available."""
        # Fill all slots
        runs = []
        tickets = []
        for i in range(3):
            ticket = Ticket(
                id=str(uuid4()),
                external_id=f"feature-{i}",
                workspace_id=workspace.id,
                title=f"Test feature {i}",
                state=TicketState.IN_PROGRESS,
                work_item_type=WorkItemType.FEATURE,
            )
            session.add(ticket)
            session.flush()

            run = AgentRun(
                id=str(uuid4()),
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                agent_id="test-agent",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            session.flush()

            result = await queue_service.queue_run(
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                run_id=run.id,
            )
            runs.append((run, result))
            tickets.append(ticket)

        session.commit()

        # First 3 should be started
        for i, (run, result) in enumerate(runs[:3]):
            assert result["status"] == "started"

        # Create 4th run - should be queued
        ticket4 = Ticket(
            id=str(uuid4()),
            external_id="feature-4",
            workspace_id=workspace.id,
            title="Test feature 4",
            state=TicketState.IN_PROGRESS,
            work_item_type=WorkItemType.FEATURE,
        )
        session.add(ticket4)
        session.flush()

        run4 = AgentRun(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket4.id,
            agent_id="test-agent",
            status=RunStatus.RUNNING,
        )
        session.add(run4)
        session.commit()

        result4 = await queue_service.queue_run(
            workspace_id=workspace.id,
            ticket_id=ticket4.id,
            run_id=run4.id,
        )

        assert result4["status"] == "queued"
        assert result4["position"] == 1
        assert result4["queue_length"] == 1

    @pytest.mark.asyncio
    async def test_queue_run_multiple_in_queue(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
        session: Session,
    ):
        """Test multiple runs get queued in order."""
        # Fill slots first
        for i in range(3):
            ticket = Ticket(
                id=str(uuid4()),
                external_id=f"feature-{i}",
                workspace_id=workspace.id,
                title=f"Test {i}",
                state=TicketState.IN_PROGRESS,
                work_item_type=WorkItemType.FEATURE,
            )
            session.add(ticket)
            session.flush()

            run = AgentRun(
                id=str(uuid4()),
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                agent_id="test-agent",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            session.flush()

            await queue_service.queue_run(
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                run_id=run.id,
            )

        session.commit()

        # Queue 3 more runs
        for i in range(3, 6):
            ticket = Ticket(
                id=str(uuid4()),
                external_id=f"feature-{i}",
                workspace_id=workspace.id,
                title=f"Test {i}",
                state=TicketState.IN_PROGRESS,
                work_item_type=WorkItemType.FEATURE,
            )
            session.add(ticket)
            session.flush()

            run = AgentRun(
                id=str(uuid4()),
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                agent_id="test-agent",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            session.flush()

            result = await queue_service.queue_run(
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                run_id=run.id,
            )

            # Should be queued at position i - 2 (0, 1, 2 = started, 3, 4, 5 = queued at 1, 2, 3)
            assert result["status"] == "queued"
            assert result["position"] == i - 2

        session.commit()


class TestGetActiveRuns:
    """Test fetching active runs."""

    @pytest.mark.asyncio
    async def test_get_active_runs_empty(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
    ):
        """Test get_active_runs when no runs active."""
        active_runs = await queue_service.get_active_runs(workspace.id)

        assert active_runs == []

    @pytest.mark.asyncio
    async def test_get_active_runs_returns_running(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
        ticket: Ticket,
        agent_run: AgentRun,
    ):
        """Test get_active_runs returns active runs."""
        await queue_service.queue_run(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_id=agent_run.id,
        )

        active_runs = await queue_service.get_active_runs(workspace.id)

        assert len(active_runs) == 1
        assert active_runs[0]["run_id"] == agent_run.id
        assert active_runs[0]["slot_number"] == 1
        assert active_runs[0]["agent_id"] == "test-agent"


class TestGetQueuedRuns:
    """Test fetching queued runs."""

    @pytest.mark.asyncio
    async def test_get_queued_runs_empty(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
    ):
        """Test get_queued_runs when queue empty."""
        queued_runs = await queue_service.get_queued_runs(workspace.id)

        assert queued_runs == []

    @pytest.mark.asyncio
    async def test_get_queued_runs_returns_queued(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
        session: Session,
    ):
        """Test get_queued_runs returns queued runs."""
        # Fill slots
        for i in range(3):
            ticket = Ticket(
                id=str(uuid4()),
                external_id=f"feature-{i}",
                workspace_id=workspace.id,
                title=f"Test {i}",
                state=TicketState.IN_PROGRESS,
                work_item_type=WorkItemType.FEATURE,
            )
            session.add(ticket)
            session.flush()

            run = AgentRun(
                id=str(uuid4()),
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                agent_id="test-agent",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            session.flush()

            await queue_service.queue_run(
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                run_id=run.id,
            )

        session.commit()

        # Queue one more
        ticket = Ticket(
            id=str(uuid4()),
            external_id="feature-queued",
            workspace_id=workspace.id,
            title="Queued",
            state=TicketState.IN_PROGRESS,
            work_item_type=WorkItemType.FEATURE,
        )
        session.add(ticket)
        session.flush()

        run = AgentRun(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            agent_id="test-agent",
            status=RunStatus.QUEUED,
        )
        session.add(run)
        session.flush()

        await queue_service.queue_run(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_id=run.id,
        )

        session.commit()

        queued_runs = await queue_service.get_queued_runs(workspace.id)

        assert len(queued_runs) == 1
        assert queued_runs[0]["position"] == 1


class TestPromoteFromQueue:
    """Test queue promotion."""

    @pytest.mark.asyncio
    async def test_promote_from_queue_success(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
        session: Session,
    ):
        """Test successful promotion from queue."""
        # Fill slots
        for i in range(3):
            ticket = Ticket(
                id=str(uuid4()),
                external_id=f"feature-{i}",
                workspace_id=workspace.id,
                title=f"Test {i}",
                state=TicketState.IN_PROGRESS,
                work_item_type=WorkItemType.FEATURE,
            )
            session.add(ticket)
            session.flush()

            run = AgentRun(
                id=str(uuid4()),
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                agent_id="test-agent",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            session.flush()

            await queue_service.queue_run(
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                run_id=run.id,
            )

        session.commit()

        # Queue one run
        ticket = Ticket(
            id=str(uuid4()),
            external_id="feature-queued",
            workspace_id=workspace.id,
            title="Queued",
            state=TicketState.IN_PROGRESS,
            work_item_type=WorkItemType.FEATURE,
        )
        session.add(ticket)
        session.flush()

        run = AgentRun(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            agent_id="test-agent",
            status=RunStatus.QUEUED,
        )
        session.add(run)
        session.commit()

        await queue_service.queue_run(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_id=run.id,
        )

        # Now promote
        promoted = await queue_service.promote_from_queue(workspace.id)

        assert promoted is not None
        assert promoted["run_id"] == run.id
        assert "promoted" in promoted["message"].lower()

    @pytest.mark.asyncio
    async def test_promote_from_queue_no_runs(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
    ):
        """Test promote when no queued runs."""
        promoted = await queue_service.promote_from_queue(workspace.id)

        assert promoted is None


class TestOnRunComplete:
    """Test run completion handling."""

    @pytest.mark.asyncio
    async def test_on_run_complete_frees_slot_and_promotes(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
        ticket: Ticket,
        agent_run: AgentRun,
        session: Session,
    ):
        """Test on_run_complete frees slot and promotes from queue."""
        # Start a run
        await queue_service.queue_run(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_id=agent_run.id,
        )

        # Queue another
        ticket2 = Ticket(
            id=str(uuid4()),
            external_id="feature-2",
            workspace_id=workspace.id,
            title="Test 2",
            state=TicketState.IN_PROGRESS,
            work_item_type=WorkItemType.FEATURE,
        )
        session.add(ticket2)
        session.flush()

        run2 = AgentRun(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket2.id,
            agent_id="test-agent",
            status=RunStatus.QUEUED,
        )
        session.add(run2)
        session.commit()

        await queue_service.queue_run(
            workspace_id=workspace.id,
            ticket_id=ticket2.id,
            run_id=run2.id,
        )

        # Complete first run
        result = await queue_service.on_run_complete(workspace.id, agent_run.id)

        assert result is not None
        assert result["status"] == "promoted"
        assert result["next_run"]["run_id"] == run2.id


class TestCancelQueuedRun:
    """Test cancelling queued runs."""

    @pytest.mark.asyncio
    async def test_cancel_queued_run_success(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
        session: Session,
    ):
        """Test successful cancellation of queued run."""
        # Fill slots and queue a run
        for i in range(3):
            ticket = Ticket(
                id=str(uuid4()),
                external_id=f"feature-{i}",
                workspace_id=workspace.id,
                title=f"Test {i}",
                state=TicketState.IN_PROGRESS,
                work_item_type=WorkItemType.FEATURE,
            )
            session.add(ticket)
            session.flush()

            run = AgentRun(
                id=str(uuid4()),
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                agent_id="test-agent",
                status=RunStatus.RUNNING,
            )
            session.add(run)
            session.flush()

            await queue_service.queue_run(
                workspace_id=workspace.id,
                ticket_id=ticket.id,
                run_id=run.id,
            )

        session.commit()

        # Queue a run to cancel
        ticket = Ticket(
            id=str(uuid4()),
            external_id="feature-cancel",
            workspace_id=workspace.id,
            title="To cancel",
            state=TicketState.IN_PROGRESS,
            work_item_type=WorkItemType.FEATURE,
        )
        session.add(ticket)
        session.flush()

        run = AgentRun(
            id=str(uuid4()),
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            agent_id="test-agent",
            status=RunStatus.QUEUED,
        )
        session.add(run)
        session.commit()

        await queue_service.queue_run(
            workspace_id=workspace.id,
            ticket_id=ticket.id,
            run_id=run.id,
        )

        # Cancel it
        cancelled = await queue_service.cancel_queued_run(run.id)

        assert cancelled is True


class TestGetQueueStats:
    """Test queue statistics."""

    def test_get_queue_stats_empty(
        self,
        queue_service: ParallelQueueService,
        workspace: Workspace,
    ):
        """Test queue stats when empty."""
        stats = queue_service.get_queue_stats(workspace.id)

        assert stats["max_concurrent"] == 3
        assert stats["active_count"] == 0
        assert stats["available_slots"] == 3
        assert stats["queued_count"] == 0


if __name__ == "__main__":
    pytest.main([__file__, "-v"])
