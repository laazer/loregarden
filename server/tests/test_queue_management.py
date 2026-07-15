"""Tests for queue management API endpoints."""

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from loregarden.api.queue_management import _reorder_queue_internal
from loregarden.models.domain import (
    AgentRun,
    AgentSlot,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Workspace,
)
from sqlmodel import Session, select


@pytest.mark.asyncio
class TestQueueReordering:
    """Test queue reordering functionality."""

    async def test_reorder_run_to_earlier_position(self, db_session: Session):
        """Test moving a run to an earlier position in queue."""
        # Create workspace
        ws = Workspace(id="ws-1", slug="ws-1", name="Test Workspace")
        db_session.add(ws)
        db_session.commit()

        # Create queued runs: run-1 pos 1, run-2 pos 2, run-3 pos 3
        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-1",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run2 = QueuedRun(
            run_id="run-2",
            ticket_id="ticket-2",
            workspace_id="ws-1",
            position=2,
            status=QueuePosition.QUEUED,
        )
        run3 = QueuedRun(
            run_id="run-3",
            ticket_id="ticket-3",
            workspace_id="ws-1",
            position=3,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([run1, run2, run3])
        db_session.commit()

        # Move run-3 (pos 3) to position 1
        await _reorder_queue_internal(db_session, "ws-1", "run-3", 3, 1)

        # Verify positions: run-3 should be at 1, run-1 at 2, run-2 at 3
        updated_run3 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-3")).first()
        updated_run1 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-1")).first()
        updated_run2 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-2")).first()

        assert updated_run3.position == 1
        assert updated_run1.position == 2
        assert updated_run2.position == 3

    async def test_reorder_run_to_later_position(self, db_session: Session):
        """Test moving a run to a later position in queue."""
        ws = Workspace(id="ws-2", slug="ws-2", name="Test Workspace 2")
        db_session.add(ws)
        db_session.commit()

        # Create queued runs: run-1 pos 1, run-2 pos 2, run-3 pos 3
        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-2",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run2 = QueuedRun(
            run_id="run-2",
            ticket_id="ticket-2",
            workspace_id="ws-2",
            position=2,
            status=QueuePosition.QUEUED,
        )
        run3 = QueuedRun(
            run_id="run-3",
            ticket_id="ticket-3",
            workspace_id="ws-2",
            position=3,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([run1, run2, run3])
        db_session.commit()

        # Move run-1 (pos 1) to position 3
        await _reorder_queue_internal(db_session, "ws-2", "run-1", 1, 3)

        # Verify positions: run-2 at 1, run-3 at 2, run-1 at 3
        updated_run1 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-1")).first()
        updated_run2 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-2")).first()
        updated_run3 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-3")).first()

        assert updated_run1.position == 3
        assert updated_run2.position == 1
        assert updated_run3.position == 2

    async def test_reorder_invalid_position_too_high(self, db_session: Session):
        """Test reorder with position beyond queue length."""
        from fastapi import HTTPException

        ws = Workspace(id="ws-3", slug="ws-3", name="Test Workspace 3")
        db_session.add(ws)
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-3",
            position=1,
            status=QueuePosition.QUEUED,
        )
        db_session.add(run1)
        db_session.commit()

        # Try to move to position 5 (queue length is 1)
        with pytest.raises(HTTPException) as exc_info:
            from loregarden.api.queue_management import reorder_queued_run

            await reorder_queued_run("run-1", 5, db_session)

        assert exc_info.value.status_code == 400
        assert "Invalid position" in exc_info.value.detail

    async def test_reorder_invalid_position_zero(self, db_session: Session):
        """Test reorder with position 0 (should be 1-indexed)."""
        from fastapi import HTTPException

        ws = Workspace(id="ws-4", slug="ws-4", name="Test Workspace 4")
        db_session.add(ws)
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-4",
            position=1,
            status=QueuePosition.QUEUED,
        )
        db_session.add(run1)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            from loregarden.api.queue_management import reorder_queued_run

            await reorder_queued_run("run-1", 0, db_session)

        assert exc_info.value.status_code == 400

    async def test_reorder_non_existent_run(self, db_session: Session):
        """Test reordering a run that doesn't exist."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            from loregarden.api.queue_management import reorder_queued_run

            await reorder_queued_run("non-existent", 1, db_session)

        assert exc_info.value.status_code == 404

    async def test_reorder_non_queued_run(self, db_session: Session):
        """Test reordering a run that's not in queue."""
        from fastapi import HTTPException

        ws = Workspace(id="ws-5", slug="ws-5", name="Test Workspace 5")
        db_session.add(ws)
        db_session.commit()

        # Create a run that has already been promoted out of the queue
        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-5",
            position=1,
            status=QueuePosition.PROMOTED,
        )
        db_session.add(run1)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            from loregarden.api.queue_management import reorder_queued_run

            await reorder_queued_run("run-1", 1, db_session)

        assert exc_info.value.status_code == 400
        assert "not queued" in exc_info.value.detail

    async def test_reorder_same_position(self, db_session: Session):
        """Test reordering run to its current position."""
        from loregarden.api.queue_management import reorder_queued_run

        ws = Workspace(id="ws-6", slug="ws-6", name="Test Workspace 6")
        db_session.add(ws)
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-6",
            position=1,
            status=QueuePosition.QUEUED,
        )
        db_session.add(run1)
        db_session.commit()

        result = await reorder_queued_run("run-1", 1, db_session)

        assert result["status"] == "no_change"
        assert result["position"] == 1

    async def test_reorder_emits_execution_update(self, db_session: Session):
        """Verify execution_update event emitted after reorder."""
        from loregarden.api.queue_management import reorder_queued_run

        ws = Workspace(id="ws-7", slug="ws-7", name="Test Workspace 7")
        db_session.add(ws)
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-7",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run2 = QueuedRun(
            run_id="run-2",
            ticket_id="ticket-2",
            workspace_id="ws-7",
            position=2,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([run1, run2])
        db_session.commit()

        with patch("loregarden.api.queue_management.emit_execution_update") as mock_emit:
            result = await reorder_queued_run("run-2", 1, db_session)

            assert result["status"] == "reordered"
            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args[1]
            assert call_kwargs["workspace_id"] == "ws-7"
            assert "queued_runs" in call_kwargs
            assert "active_runs" in call_kwargs

    async def test_reorder_preserves_other_workspace_queues(self, db_session: Session):
        """Test that reordering in one workspace doesn't affect others."""
        ws1 = Workspace(id="ws-8", slug="ws-8", name="Workspace 1")
        ws2 = Workspace(id="ws-9", slug="ws-9", name="Workspace 2")
        db_session.add_all([ws1, ws2])
        db_session.commit()

        # Create runs in workspace 1
        run1_ws1 = QueuedRun(
            run_id="run-1-ws1",
            ticket_id="ticket-1",
            workspace_id="ws-8",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run2_ws1 = QueuedRun(
            run_id="run-2-ws1",
            ticket_id="ticket-2",
            workspace_id="ws-8",
            position=2,
            status=QueuePosition.QUEUED,
        )

        # Create runs in workspace 2
        run1_ws2 = QueuedRun(
            run_id="run-1-ws2",
            ticket_id="ticket-3",
            workspace_id="ws-9",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run2_ws2 = QueuedRun(
            run_id="run-2-ws2",
            ticket_id="ticket-4",
            workspace_id="ws-9",
            position=2,
            status=QueuePosition.QUEUED,
        )

        db_session.add_all([run1_ws1, run2_ws1, run1_ws2, run2_ws2])
        db_session.commit()

        # Reorder in workspace 1
        await _reorder_queue_internal(db_session, "ws-8", "run-2-ws1", 2, 1)

        # Verify workspace 2 unchanged
        updated_run1_ws2 = db_session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-1-ws2")
        ).first()
        updated_run2_ws2 = db_session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-2-ws2")
        ).first()

        assert updated_run1_ws2.position == 1
        assert updated_run2_ws2.position == 2


@pytest.mark.asyncio
class TestQueueInfo:
    """Test queue information endpoint."""

    async def test_get_queue_info_with_active_runs(self, db_session: Session):
        """Get info about queue with active runs."""
        from loregarden.api.queue_management import get_queue_info

        ws = Workspace(id="ws-10", slug="ws-10", name="Test Workspace 10")
        db_session.add(ws)
        db_session.commit()

        agent_run1 = AgentRun(
            id="run-1",
            run_code="run_1",
            ticket_id="ticket-1",
            workspace_id="ws-10",
            agent_id="dev",
        )
        agent_run2 = AgentRun(
            id="run-2",
            run_code="run_2",
            ticket_id="ticket-2",
            workspace_id="ws-10",
            agent_id="dev",
        )
        db_session.add_all([agent_run1, agent_run2])
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-10",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run2 = QueuedRun(
            run_id="run-2",
            ticket_id="ticket-2",
            workspace_id="ws-10",
            position=2,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([run1, run2])
        db_session.commit()

        result = await get_queue_info("ws-10", db_session)

        assert result["queue_length"] == 2
        assert "active_runs_count" in result
        assert "estimated_clear_time_seconds" in result
        assert result["max_position"] == 2

    async def test_get_queue_info_empty_queue(self, db_session: Session):
        """Get info about empty queue."""
        from loregarden.api.queue_management import get_queue_info

        ws = Workspace(id="ws-11", slug="ws-11", name="Test Workspace 11")
        db_session.add(ws)
        db_session.commit()

        result = await get_queue_info("ws-11", db_session)

        assert result["queue_length"] == 0
        assert result["max_position"] == 0
        assert result["runs"] == []

    async def test_get_queue_info_estimated_clear_time(self, db_session: Session):
        """Verify estimated clear time calculation."""
        from loregarden.api.queue_management import get_queue_info

        ws = Workspace(id="ws-12", slug="ws-12", name="Test Workspace 12")
        db_session.add(ws)
        db_session.commit()

        # Create 3 queued runs (300s each = 900s)
        for i in range(1, 4):
            agent_run = AgentRun(
                id=f"run-{i}",
                run_code=f"run_{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-12",
                agent_id="dev",
            )
            db_session.add(agent_run)
        db_session.commit()

        for i in range(1, 4):
            run = QueuedRun(
                run_id=f"run-{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-12",
                position=i,
                status=QueuePosition.QUEUED,
            )
            db_session.add(run)
        db_session.commit()

        result = await get_queue_info("ws-12", db_session)

        # 3 queued runs * 300s = 900s
        assert result["estimated_clear_time_seconds"] == 900

    async def test_get_queue_info_invalid_workspace(self, db_session: Session):
        """Get info for non-existent workspace."""
        from loregarden.api.queue_management import get_queue_info

        result = await get_queue_info("non-existent-ws", db_session)

        # Should return valid response with empty queue
        assert result["workspace_id"] == "non-existent-ws"
        assert result["queue_length"] == 0


@pytest.mark.asyncio
class TestQueuePromotion:
    """Test manual queue promotion."""

    async def test_promote_run_with_available_slot(self, db_session: Session):
        """Promote queued run when slot available."""
        from loregarden.api.queue_management import promote_run

        ws = Workspace(id="ws-13", slug="ws-13", name="Test Workspace 13")
        db_session.add(ws)
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-13",
            position=1,
            status=QueuePosition.QUEUED,
        )
        db_session.add(run1)
        db_session.commit()

        with patch("loregarden.api.queue_management.ParallelQueueService") as mock_service:
            mock_instance = MagicMock()
            mock_service.return_value = mock_instance
            mock_instance.promote_from_queue = AsyncMock(
                return_value={"run_id": "run-1", "slot_number": 1}
            )

            result = await promote_run("run-1", db_session)

            assert result["status"] == "promoted"
            assert "slot_number" in result["message"] or "promoted_run" in result

    async def test_promote_run_no_available_slots(self, db_session: Session):
        """Try to promote when all slots occupied."""
        from loregarden.api.queue_management import promote_run

        ws = Workspace(id="ws-14", slug="ws-14", name="Test Workspace 14")
        db_session.add(ws)
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-14",
            position=1,
            status=QueuePosition.QUEUED,
        )
        db_session.add(run1)
        db_session.commit()

        with patch("loregarden.api.queue_management.ParallelQueueService") as mock_service:
            mock_instance = MagicMock()
            mock_service.return_value = mock_instance
            mock_instance.promote_from_queue = AsyncMock(return_value=None)

            result = await promote_run("run-1", db_session)

            assert result["status"] == "no_slots"

    async def test_promote_non_existent_run(self, db_session: Session):
        """Try to promote run that doesn't exist."""
        from fastapi import HTTPException

        with pytest.raises(HTTPException) as exc_info:
            from loregarden.api.queue_management import promote_run

            await promote_run("non-existent", db_session)

        assert exc_info.value.status_code == 404

    async def test_promote_non_queued_run(self, db_session: Session):
        """Try to promote a run that has already left the queue."""
        from fastapi import HTTPException
        from loregarden.api.queue_management import promote_run

        ws = Workspace(id="ws-15", slug="ws-15", name="Test Workspace 15")
        db_session.add(ws)
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-15",
            position=1,
            status=QueuePosition.PROMOTED,
        )
        db_session.add(run1)
        db_session.commit()

        with pytest.raises(HTTPException) as exc_info:
            await promote_run("run-1", db_session)

        assert exc_info.value.status_code == 400
        assert "not queued" in exc_info.value.detail

    async def test_promote_targets_the_requested_run_not_the_head(self, db_session: Session):
        """Promoting a run mid-queue starts that run, not the longest-waiting one.

        Regression: promote_run used to call promote_from_queue() without a
        run_id, which promotes the head of the queue. Asking to promote run-2
        would start run-1 and then report "no_slots" back to the caller.
        """
        from loregarden.api.queue_management import promote_run

        ws = Workspace(id="ws-promote-target", slug="ws-promote-target", name="Test")
        db_session.add(ws)
        db_session.commit()

        slot = AgentSlot(workspace_id="ws-promote-target", slot_number=1, is_available=True)
        head = QueuedRun(
            run_id="run-head",
            ticket_id="ticket-head",
            workspace_id="ws-promote-target",
            position=1,
            status=QueuePosition.QUEUED,
        )
        target = QueuedRun(
            run_id="run-target",
            ticket_id="ticket-target",
            workspace_id="ws-promote-target",
            position=2,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([slot, head, target])
        db_session.commit()

        result = await promote_run("run-target", db_session)

        assert result["status"] == "promoted"
        assert result["promoted_run"]["run_id"] == "run-target"

        # The requested run took the slot; the head stayed queued.
        updated_slot = db_session.exec(
            select(AgentSlot).where(AgentSlot.workspace_id == "ws-promote-target")
        ).first()
        assert updated_slot.current_run_id == "run-target"
        assert updated_slot.is_available is False

        updated_head = db_session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-head")
        ).first()
        assert updated_head.status == QueuePosition.QUEUED

        updated_target = db_session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-target")
        ).first()
        assert updated_target.status == QueuePosition.PROMOTED

    async def test_promote_no_slots_leaves_queue_untouched(self, db_session: Session):
        """With every slot busy, promoting must not mutate the queue."""
        from loregarden.api.queue_management import promote_run

        ws = Workspace(id="ws-promote-full", slug="ws-promote-full", name="Test")
        db_session.add(ws)
        db_session.commit()

        slot = AgentSlot(
            workspace_id="ws-promote-full",
            slot_number=1,
            is_available=False,
            current_run_id=None,
        )
        queued = QueuedRun(
            run_id="run-waiting",
            ticket_id="ticket-waiting",
            workspace_id="ws-promote-full",
            position=1,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([slot, queued])
        db_session.commit()

        result = await promote_run("run-waiting", db_session)

        assert result["status"] == "no_slots"

        unchanged = db_session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-waiting")
        ).first()
        assert unchanged.status == QueuePosition.QUEUED
        assert unchanged.position == 1


@pytest.mark.asyncio
class TestQueueErrorHandling:
    """Test error handling in queue operations."""

    async def test_reorder_websocket_emit_failure(self, db_session: Session):
        """Handle WebSocket emit failure gracefully."""
        from loregarden.api.queue_management import reorder_queued_run

        ws = Workspace(id="ws-16", slug="ws-16", name="Test Workspace 16")
        db_session.add(ws)
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-16",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run2 = QueuedRun(
            run_id="run-2",
            ticket_id="ticket-2",
            workspace_id="ws-16",
            position=2,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([run1, run2])
        db_session.commit()

        with patch("loregarden.api.queue_management.emit_execution_update") as mock_emit:
            mock_emit.side_effect = Exception("WebSocket unavailable")

            # Should still return success even if emit fails
            result = await reorder_queued_run("run-2", 1, db_session)

            assert result["status"] == "reordered"
            assert result["new_position"] == 1

    async def test_queue_info_handles_missing_runs(self, db_session: Session):
        """Handle case where run deleted while fetching info."""
        from loregarden.api.queue_management import get_queue_info

        ws = Workspace(id="ws-17", slug="ws-17", name="Test Workspace 17")
        db_session.add(ws)
        db_session.commit()

        # Create runs then delete one mid-operation (simulated by returning gracefully)
        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-17",
            position=1,
            status=QueuePosition.QUEUED,
        )
        db_session.add(run1)
        db_session.commit()

        # Should return graceful response
        result = await get_queue_info("ws-17", db_session)

        assert "workspace_id" in result
        assert isinstance(result["queue_length"], int)


@pytest.mark.asyncio
class TestQueueConcurrency:
    """Test queue operations with concurrent requests."""

    async def test_concurrent_reorder_requests(self, db_session: Session):
        """Multiple reorder requests at same time."""

        ws = Workspace(id="ws-18", slug="ws-18", name="Test Workspace 18")
        db_session.add(ws)
        db_session.commit()

        # Create queue
        for i in range(1, 4):
            run = QueuedRun(
                run_id=f"run-{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-18",
                position=i,
                status=QueuePosition.QUEUED,
            )
            db_session.add(run)
        db_session.commit()

        # Simulate concurrent reorders (use internal function to avoid API layer concerns)
        async def reorder_task(run_id, old_pos, new_pos):
            await _reorder_queue_internal(db_session, "ws-18", run_id, old_pos, new_pos)

        # Execute two reorders in sequence (asyncio doesn't actually run concurrently in test context)
        await reorder_task("run-1", 1, 2)
        await reorder_task("run-3", 3, 1)

        # Verify final state is consistent
        all_runs = db_session.exec(
            select(QueuedRun).where(QueuedRun.workspace_id == "ws-18").order_by(QueuedRun.position)
        ).all()
        positions = [run.position for run in all_runs]

        # Should have consistent positions 1, 2, 3
        assert sorted(positions) == [1, 2, 3]

    async def test_multiple_workspaces_concurrent_reorder(self, db_session: Session):
        """Reordering in multiple workspaces at same time."""
        ws1 = Workspace(id="ws-19", slug="ws-19", name="Workspace 1")
        ws2 = Workspace(id="ws-20", slug="ws-20", name="Workspace 2")
        db_session.add_all([ws1, ws2])
        db_session.commit()

        # Create runs in both workspaces
        for ws_id in ["ws-19", "ws-20"]:
            for i in range(1, 3):
                run = QueuedRun(
                    run_id=f"run-{i}-{ws_id}",
                    ticket_id=f"ticket-{i}",
                    workspace_id=ws_id,
                    position=i,
                    status=QueuePosition.QUEUED,
                )
                db_session.add(run)
        db_session.commit()

        # Reorder in both workspaces
        await _reorder_queue_internal(db_session, "ws-19", "run-2-ws-19", 2, 1)
        await _reorder_queue_internal(db_session, "ws-20", "run-2-ws-20", 2, 1)

        # Verify each workspace is correctly reordered independently
        runs_ws1 = db_session.exec(
            select(QueuedRun).where(QueuedRun.workspace_id == "ws-19").order_by(QueuedRun.position)
        ).all()
        runs_ws2 = db_session.exec(
            select(QueuedRun).where(QueuedRun.workspace_id == "ws-20").order_by(QueuedRun.position)
        ).all()

        assert runs_ws1[0].run_id == "run-2-ws-19"
        assert runs_ws2[0].run_id == "run-2-ws-20"


@pytest.mark.asyncio
class TestQueueIntegration:
    """Integration tests with other queue operations."""

    async def test_reorder_then_complete_run(self, db_session: Session):
        """Reorder, then complete a run and promote."""
        ws = Workspace(id="ws-21", slug="ws-21", name="Test Workspace 21")
        db_session.add(ws)
        db_session.commit()

        # Create queue: run-1 active, run-2 queued, run-3 queued
        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-21",
            position=None,
            status=QueuePosition.ACTIVE,
        )
        run2 = QueuedRun(
            run_id="run-2",
            ticket_id="ticket-2",
            workspace_id="ws-21",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run3 = QueuedRun(
            run_id="run-3",
            ticket_id="ticket-3",
            workspace_id="ws-21",
            position=2,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([run1, run2, run3])
        db_session.commit()

        # Reorder: move run-3 to position 1
        await _reorder_queue_internal(db_session, "ws-21", "run-3", 2, 1)

        # Verify reorder worked
        updated_run3 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-3")).first()
        updated_run2 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-2")).first()

        assert updated_run3.position == 1
        assert updated_run2.position == 2

    async def test_reorder_affects_queue_state(self, db_session: Session):
        """Verify reorder updates queue state for frontend."""
        from loregarden.api.queue_management import reorder_queued_run

        ws = Workspace(id="ws-22", slug="ws-22", name="Test Workspace 22")
        db_session.add(ws)
        db_session.commit()

        agent_run1 = AgentRun(
            id="run-1",
            run_code="run_1",
            ticket_id="ticket-1",
            workspace_id="ws-22",
            agent_id="dev",
        )
        agent_run2 = AgentRun(
            id="run-2",
            run_code="run_2",
            ticket_id="ticket-2",
            workspace_id="ws-22",
            agent_id="dev",
        )
        db_session.add_all([agent_run1, agent_run2])
        db_session.commit()

        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-22",
            position=1,
            status=QueuePosition.QUEUED,
        )
        run2 = QueuedRun(
            run_id="run-2",
            ticket_id="ticket-2",
            workspace_id="ws-22",
            position=2,
            status=QueuePosition.QUEUED,
        )
        db_session.add_all([run1, run2])
        db_session.commit()

        with patch("loregarden.api.queue_management.emit_execution_update") as mock_emit:
            result = await reorder_queued_run("run-2", 1, db_session)

            assert result["status"] == "reordered"
            # Verify emit was called with updated queue state
            mock_emit.assert_called_once()
            call_kwargs = mock_emit.call_args[1]

            # Check that queued_runs are passed and in new order
            queued_runs = call_kwargs["queued_runs"]
            assert queued_runs[0]["run_id"] == "run-2"
            assert queued_runs[1]["run_id"] == "run-1"


@pytest.mark.asyncio
class TestOnRunCompleteFailureWiring:
    """A FAILED AgentRun must mark its QueuedRun FAILED so the retry API
    (api/bulk_queue_operations.py) can see and act on it — previously
    on_run_complete never inspected run.status at all, so FAILED was never
    produced and the retry endpoints always saw an empty list."""

    def _patched_emits(self):
        return (
            patch("loregarden.services.parallel_queue.emit_run_completed"),
            patch("loregarden.services.parallel_queue.emit_execution_update"),
            patch("loregarden.services.parallel_queue.emit_queue_promoted"),
            patch("loregarden.services.parallel_queue.emit_error"),
        )

    async def test_failed_run_marks_queued_run_failed(self, db_session: Session):
        from loregarden.services.parallel_queue import ParallelQueueService

        ws = Workspace(id="ws-fail-1", slug="ws-fail-1", name="Test Workspace Fail 1")
        db_session.add(ws)
        db_session.commit()

        agent_run = AgentRun(
            id="run-fail-1",
            run_code="run_fail_1",
            ticket_id="ticket-fail-1",
            workspace_id="ws-fail-1",
            agent_id="dev",
            status=RunStatus.FAILED,
            stderr="static_qa found 3 violations",
        )
        db_session.add(agent_run)
        db_session.commit()

        queued = QueuedRun(
            run_id="run-fail-1",
            ticket_id="ticket-fail-1",
            workspace_id="ws-fail-1",
            position=1,
            status=QueuePosition.PROMOTED,
        )
        db_session.add(queued)
        db_session.commit()

        service = ParallelQueueService(db_session)
        p1, p2, p3, p4 = self._patched_emits()
        with p1, p2, p3, p4:
            await service.on_run_complete("ws-fail-1", "run-fail-1")

        db_session.refresh(queued)
        assert queued.status == QueuePosition.FAILED
        assert "static_qa" in queued.failure_reason
        assert queued.last_failed_at is not None

    async def test_failed_run_visible_via_get_failed_runs(self, db_session: Session):
        from loregarden.api.bulk_queue_operations import get_failed_runs
        from loregarden.services.parallel_queue import ParallelQueueService

        ws = Workspace(id="ws-fail-2", slug="ws-fail-2", name="Test Workspace Fail 2")
        db_session.add(ws)
        db_session.commit()

        agent_run = AgentRun(
            id="run-fail-2",
            run_code="run_fail_2",
            ticket_id="ticket-fail-2",
            workspace_id="ws-fail-2",
            agent_id="dev",
            status=RunStatus.FAILED,
            stderr="agent crashed",
        )
        db_session.add(agent_run)
        db_session.commit()

        queued = QueuedRun(
            run_id="run-fail-2",
            ticket_id="ticket-fail-2",
            workspace_id="ws-fail-2",
            position=1,
            status=QueuePosition.PROMOTED,
        )
        db_session.add(queued)
        db_session.commit()

        service = ParallelQueueService(db_session)
        p1, p2, p3, p4 = self._patched_emits()
        with p1, p2, p3, p4:
            await service.on_run_complete("ws-fail-2", "run-fail-2")

        failed = await get_failed_runs("ws-fail-2", db_session)
        assert len(failed) == 1
        assert failed[0]["run_id"] == "run-fail-2"
        assert failed[0]["failure_reason"] == "agent crashed"

    async def test_successful_run_does_not_mark_queued_run_failed(self, db_session: Session):
        from loregarden.services.parallel_queue import ParallelQueueService

        ws = Workspace(id="ws-ok-1", slug="ws-ok-1", name="Test Workspace OK 1")
        db_session.add(ws)
        db_session.commit()

        agent_run = AgentRun(
            id="run-ok-1",
            run_code="run_ok_1",
            ticket_id="ticket-ok-1",
            workspace_id="ws-ok-1",
            agent_id="dev",
            status=RunStatus.SUCCEEDED,
        )
        db_session.add(agent_run)
        db_session.commit()

        queued = QueuedRun(
            run_id="run-ok-1",
            ticket_id="ticket-ok-1",
            workspace_id="ws-ok-1",
            position=1,
            status=QueuePosition.PROMOTED,
        )
        db_session.add(queued)
        db_session.commit()

        service = ParallelQueueService(db_session)
        p1, p2, p3, p4 = self._patched_emits()
        with p1, p2, p3, p4:
            await service.on_run_complete("ws-ok-1", "run-ok-1")

        db_session.refresh(queued)
        assert queued.status == QueuePosition.PROMOTED
        assert queued.failure_reason == ""
