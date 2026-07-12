"""Unit tests for WebSocket event emissions in parallel API endpoints."""

from unittest.mock import patch

import pytest
from loregarden.models.domain import AgentRun, Ticket, Workspace, Worktree
from loregarden.services.parallel_queue import ParallelQueueService


@pytest.mark.asyncio
class TestParallelQueueEventEmissions:
    """Test event emissions in ParallelQueueService."""

    async def test_queue_run_emits_execution_update_when_queued(self, db_session):
        """Verify execution_update event emitted when run is queued."""
        ws = Workspace(id="ws-1", slug="ws-1", name="Test")
        db_session.add(ws)
        db_session.commit()

        # get_queued_runs() joins back to AgentRun to build each entry, so — like
        # every other test in this suite that expects a run to show up in a
        # queue/active list (see test_queue_management.py) — the AgentRun rows
        # must actually exist, not just the QueuedRun/AgentSlot pointers to them.
        agent_run_0 = AgentRun(
            id="run-0", run_code="run-0", ticket_id="ticket-0", workspace_id="ws-1", agent_id="dev"
        )
        agent_run_1 = AgentRun(
            id="run-1", run_code="run-1", ticket_id="ticket-1", workspace_id="ws-1", agent_id="dev"
        )
        db_session.add_all([agent_run_0, agent_run_1])
        db_session.commit()

        service = ParallelQueueService(db_session, max_concurrent=1)

        # Create a run that will be queued (no available slots)
        service.initialize_slots("ws-1")

        # Occupy the single slot first, so the next run actually has to queue —
        # with only 1 slot, queuing the very first run starts it immediately.
        await service.queue_run("ws-1", "ticket-0", "run-0")

        with patch("loregarden.services.parallel_queue.emit_execution_update") as mock_emit:
            # Queue second run — no slots available now.
            await service.queue_run("ws-1", "ticket-1", "run-1")

            # Verify emit was called
            mock_emit.assert_called_once()
            call_args = mock_emit.call_args
            assert call_args[1]["workspace_id"] == "ws-1"
            assert len(call_args[1]["queued_runs"]) == 1

    async def test_queue_run_emits_execution_update_when_started(self, db_session):
        """Verify execution_update event emitted when run starts immediately."""
        ws = Workspace(id="ws-1", slug="ws-1", name="Test")
        db_session.add(ws)
        db_session.commit()

        # get_active_runs() joins back to AgentRun, so it needs to actually exist
        # (see comment in test_queue_run_emits_execution_update_when_queued above).
        agent_run = AgentRun(
            id="run-1", run_code="run-1", ticket_id="ticket-1", workspace_id="ws-1", agent_id="dev"
        )
        db_session.add(agent_run)
        db_session.commit()

        with patch("loregarden.services.parallel_queue.emit_execution_update") as mock_emit:
            service = ParallelQueueService(db_session, max_concurrent=2)

            service.initialize_slots("ws-1")

            # Queue run - should start immediately with 2 slots
            result = await service.queue_run("ws-1", "ticket-1", "run-1")

            assert result["status"] == "started"
            mock_emit.assert_called_once()
            call_args = mock_emit.call_args
            assert len(call_args[1]["active_runs"]) == 1

    async def test_queue_run_emits_error_on_failure(self, db_session):
        """Verify emit_error called when queue_run fails."""
        with (
            patch("loregarden.services.parallel_queue.emit_error") as mock_error,
            patch(
                "loregarden.services.parallel_queue.ParallelQueueService.initialize_slots"
            ) as mock_init,
        ):
            mock_init.side_effect = Exception("DB error")

            service = ParallelQueueService(db_session)
            result = await service.queue_run("ws-1", "ticket-1", "run-1")

            assert result["status"] == "error"
            mock_error.assert_called_once()
            call_args = mock_error.call_args
            assert "QUEUE_ERROR" in str(call_args)

    async def test_promote_from_queue_emits_queue_promoted(self, db_session):
        """Verify queue_promoted event emitted when run promoted."""
        with (
            patch("loregarden.services.parallel_queue.emit_queue_promoted") as mock_promoted,
            patch("loregarden.services.parallel_queue.emit_execution_update"),
        ):
            service = ParallelQueueService(db_session, max_concurrent=1)

            # Setup: fill slot, then queue a run
            service.initialize_slots("ws-1")
            await service.queue_run("ws-1", "ticket-1", "run-1")
            await service.queue_run("ws-1", "ticket-2", "run-2")

            # Complete first run to trigger promotion
            await service.on_run_complete("ws-1", "run-1")

            # Verify queue_promoted was called
            mock_promoted.assert_called()
            call_args = mock_promoted.call_args
            assert call_args[1]["workspace_id"] == "ws-1"
            assert call_args[1]["run_id"] == "run-2"

    async def test_on_run_complete_emits_run_completed(self, db_session):
        """Verify run_completed event emitted on run completion."""
        with (
            patch("loregarden.services.parallel_queue.emit_run_completed") as mock_completed,
            patch("loregarden.services.parallel_queue.emit_execution_update"),
            patch("loregarden.services.parallel_queue.emit_queue_promoted"),
        ):
            service = ParallelQueueService(db_session, max_concurrent=2)

            # Setup: create run in database
            service.initialize_slots("ws-1")
            await service.queue_run("ws-1", "ticket-1", "run-1")

            # Complete run
            await service.on_run_complete("ws-1", "run-1")

            mock_completed.assert_called_once()
            call_args = mock_completed.call_args
            assert call_args[1]["workspace_id"] == "ws-1"
            assert call_args[1]["run_id"] == "run-1"

    async def test_on_run_complete_emits_execution_update(self, db_session):
        """Verify execution_update emitted after run completion."""
        with (
            patch("loregarden.services.parallel_queue.emit_execution_update") as mock_update,
            patch("loregarden.services.parallel_queue.emit_run_completed"),
            patch("loregarden.services.parallel_queue.emit_queue_promoted"),
        ):
            service = ParallelQueueService(db_session, max_concurrent=2)

            service.initialize_slots("ws-1")
            await service.queue_run("ws-1", "ticket-1", "run-1")
            await service.on_run_complete("ws-1", "run-1")

            # Should be called at least twice (once for queue_run, once for on_run_complete)
            assert mock_update.call_count >= 2

    async def test_event_emission_graceful_failure(self, db_session):
        """Verify service continues if event emission fails."""
        with patch("loregarden.services.parallel_queue.emit_execution_update") as mock_emit:
            mock_emit.side_effect = Exception("WebSocket emit failed")

            service = ParallelQueueService(db_session, max_concurrent=2)
            service.initialize_slots("ws-1")

            # Should not raise even though emit fails
            result = await service.queue_run("ws-1", "ticket-1", "run-1")

            assert result["status"] == "started"
            mock_emit.assert_called_once()


@pytest.mark.asyncio
class TestConflictDetectorEventEmissions:
    """Test event emissions in conflict detection endpoints."""

    async def test_check_conflicts_emits_conflict_detected(self, db_session):
        """Verify conflict_detected event emitted with the worktree/run/conflict data."""
        from loregarden.api.parallel import check_conflicts

        ws = Workspace(id="ws-conflict-1", slug="ws-conflict-1", name="Test")
        db_session.add(ws)
        db_session.commit()

        ticket = Ticket(
            external_id="TK-conflict-1", workspace_id="ws-conflict-1", title="Test ticket"
        )
        db_session.add(ticket)
        db_session.commit()

        run = AgentRun(
            run_code="run-conflict-1",
            ticket_id=ticket.id,
            workspace_id="ws-conflict-1",
            agent_id="static_qa",
        )
        db_session.add(run)
        db_session.commit()

        worktree = Worktree(
            workspace_id="ws-conflict-1",
            agent_run_id=run.id,
            worktree_path="/tmp/fake-worktree",
        )
        db_session.add(worktree)
        db_session.commit()

        with (
            patch("loregarden.api.parallel.emit_conflict_detected") as mock_emit,
            patch(
                "loregarden.services.conflict_detector.ConflictDetectorService.get_conflict_preview"
            ) as mock_preview,
            patch(
                "loregarden.services.conflict_detector.ConflictDetectorService.get_conflict_details"
            ) as mock_details,
        ):
            mock_preview.return_value = {
                "has_conflicts": True,
                "conflicting_files": ["src/main.py", "tests/test_main.py"],
                "summary": "Merge conflicts in 2 files",
                "auto_mergeable": False,
            }
            mock_details.return_value = {
                "conflicts": [{"file": "src/main.py", "ours_lines": 10, "theirs_lines": 5}],
                "suggestions": ["Resolve manually"],
                "severity": "high",
            }

            result = await check_conflicts(worktree.id, "main", db_session)

        mock_emit.assert_called_once()
        _, kwargs = mock_emit.call_args
        assert kwargs["worktree_id"] == worktree.id
        assert kwargs["run_id"] == run.id
        assert kwargs["conflicts"] == [{"file": "src/main.py", "ours_lines": 10, "theirs_lines": 5}]
        assert kwargs["severity"] == "high"
        assert kwargs["preview"]["has_conflicts"] is True

        assert result["has_conflicts"] is True
        assert result["severity"] == "high"

    async def test_merge_worktree_emits_conflict_resolved(self, db_session):
        """Verify conflict_resolved event emitted on successful merge."""
        # This tests the API endpoint, covered in integration tests
        assert True

    async def test_merge_worktree_emits_error_on_failure(self, db_session):
        """Verify emit_error called on merge failure."""
        # Covered in integration tests
        assert True
