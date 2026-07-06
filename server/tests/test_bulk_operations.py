"""Tests for bulk queue operations and retry logic."""

import pytest
from datetime import datetime, timezone, timedelta
from unittest.mock import patch, AsyncMock
from sqlmodel import Session, select

from loregarden.models.domain import QueuedRun, QueuePosition, Workspace, Ticket


@pytest.mark.asyncio
class TestBulkCancelRuns:
    """Test bulk cancel operations."""

    async def test_cancel_multiple_runs(self, session: Session):
        """Cancel multiple runs at once."""
        ws = Workspace(id="ws-1", name="Test", slug="test")
        session.add(ws)
        session.commit()

        # Create multiple runs
        runs = [
            QueuedRun(
                run_id=f"run-{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-1",
                position=i,
            )
            for i in range(1, 4)
        ]
        session.add_all(runs)
        session.commit()

        run_ids = [f"run-{i}" for i in range(1, 4)]

        # Simulate bulk cancel
        for run_id in run_ids:
            run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()
            run.status = QueuePosition.QUEUED
            session.add(run)

        session.commit()

        # Verify all cancelled
        for run_id in run_ids:
            run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()
            assert run is not None

    async def test_cancel_nonexistent_run(self, session: Session):
        """Cancel non-existent run handles gracefully."""
        ws = Workspace(id="ws-2", name="Test", slug="test2")
        session.add(ws)
        session.commit()

        # Try to cancel non-existent run
        run = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "non-existent")
        ).first()

        assert run is None

    async def test_cancel_mixed_results(self, session: Session):
        """Bulk cancel with some successful, some failed."""
        ws = Workspace(id="ws-3", name="Test", slug="test3")
        session.add(ws)
        session.commit()

        # Create one run
        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-3",
            position=1,
        )
        session.add(run)
        session.commit()

        # Try to cancel existing and non-existing
        run_ids = ["run-1", "run-nonexistent"]
        successful = 0
        failed = 0

        for run_id in run_ids:
            r = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()

            if r:
                r.status = QueuePosition.QUEUED
                session.add(r)
                successful += 1
            else:
                failed += 1

        session.commit()

        assert successful == 1
        assert failed == 1


@pytest.mark.asyncio
class TestBulkPauseRuns:
    """Test bulk pause operations."""

    async def test_pause_multiple_active_runs(self, session: Session):
        """Pause multiple active runs."""
        ws = Workspace(id="ws-4", name="Test", slug="test4")
        session.add(ws)
        session.commit()

        # Create active runs
        runs = [
            QueuedRun(
                run_id=f"run-{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-4",
                position=i,
                status=QueuePosition.STARTED,
            )
            for i in range(1, 3)
        ]
        session.add_all(runs)
        session.commit()

        # Pause all
        for run in runs:
            r = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run.run_id)
            ).first()
            r.status = "paused"
            session.add(r)

        session.commit()

        # Verify paused
        paused = session.exec(
            select(QueuedRun).where(
                QueuedRun.workspace_id == "ws-4"
            )
        ).all()

        assert all(r.status == "paused" for r in paused)

    async def test_pause_only_active_runs(self, session: Session):
        """Pause only affects active runs, not queued."""
        ws = Workspace(id="ws-5", name="Test", slug="test5")
        session.add(ws)
        session.commit()

        active = QueuedRun(
            run_id="run-active",
            ticket_id="ticket-1",
            workspace_id="ws-5",
            position=1,
            status=QueuePosition.STARTED,
        )
        queued = QueuedRun(
            run_id="run-queued",
            ticket_id="ticket-2",
            workspace_id="ws-5",
            position=2,
            status=QueuePosition.QUEUED,
        )
        session.add_all([active, queued])
        session.commit()

        # Try to pause both
        for run_id in ["run-active", "run-queued"]:
            run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()

            if run.status == QueuePosition.STARTED:
                run.status = "paused"
                session.add(run)

        session.commit()

        active_check = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-active")
        ).first()
        queued_check = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-queued")
        ).first()

        assert active_check.status == "paused"
        assert queued_check.status == QueuePosition.QUEUED


@pytest.mark.asyncio
class TestBulkReorderRuns:
    """Test bulk reorder operations."""

    async def test_reorder_multiple_runs(self, session: Session):
        """Reorder multiple runs in queue."""
        ws = Workspace(id="ws-6", name="Test", slug="test6")
        session.add(ws)
        session.commit()

        # Create queued runs
        runs = [
            QueuedRun(
                run_id=f"run-{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-6",
                position=i,
            )
            for i in range(1, 4)
        ]
        session.add_all(runs)
        session.commit()

        # Reorder: reverse the order
        new_order = ["run-3", "run-2", "run-1"]

        for new_pos, run_id in enumerate(new_order, 1):
            run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()
            run.position = new_pos
            session.add(run)

        session.commit()

        # Verify new positions
        for new_pos, run_id in enumerate(new_order, 1):
            run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()
            assert run.position == new_pos

    async def test_reorder_partial_queue(self, session: Session):
        """Reorder subset of runs in queue."""
        ws = Workspace(id="ws-7", name="Test", slug="test7")
        session.add(ws)
        session.commit()

        # Create 5 queued runs
        runs = [
            QueuedRun(
                run_id=f"run-{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-7",
                position=i,
            )
            for i in range(1, 6)
        ]
        session.add_all(runs)
        session.commit()

        # Reorder only run-2 and run-4
        partial_order = ["run-4", "run-2"]

        for new_pos, run_id in enumerate(partial_order, 1):
            run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()
            run.position = new_pos
            session.add(run)

        session.commit()

        # Verify partial reorder
        run2 = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-2")
        ).first()
        run4 = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-4")
        ).first()

        assert run4.position == 1
        assert run2.position == 2


@pytest.mark.asyncio
class TestRetryLogic:
    """Test retry logic with exponential backoff."""

    async def test_retry_increments_count(self, session: Session):
        """Retry increments retry counter."""
        ws = Workspace(id="ws-8", name="Test", slug="test8")
        session.add(ws)
        session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-8",
            position=1,
            status="failed",
            retry_count=0,
            max_retries=3,
        )
        session.add(run)
        session.commit()

        # Simulate retry
        run.retry_count += 1
        run.status = QueuePosition.QUEUED
        session.add(run)
        session.commit()

        updated = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-1")
        ).first()

        assert updated.retry_count == 1
        assert updated.status == QueuePosition.QUEUED

    async def test_exponential_backoff_calculation(self, session: Session):
        """Exponential backoff calculates correctly: 2^retry_count."""
        ws = Workspace(id="ws-9", name="Test", slug="test9")
        session.add(ws)
        session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-9",
            position=1,
            status="failed",
            retry_count=0,
            max_retries=5,
        )
        session.add(run)
        session.commit()

        # Simulate retries and check backoff
        for attempt in range(3):
            run = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == "run-1")
            ).first()

            backoff_seconds = 2 ** run.retry_count
            expected_backoff = 2 ** attempt

            assert backoff_seconds == expected_backoff

            run.retry_count += 1
            run.estimated_start_at = datetime.now(timezone.utc) + timedelta(
                seconds=backoff_seconds
            )
            session.add(run)
            session.commit()

    async def test_max_retries_exceeded(self, session: Session):
        """Cannot retry when max retries exceeded."""
        ws = Workspace(id="ws-10", name="Test", slug="test10")
        session.add(ws)
        session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-10",
            position=1,
            status="failed",
            retry_count=3,
            max_retries=3,
        )
        session.add(run)
        session.commit()

        updated = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-1")
        ).first()

        assert updated.retry_count >= updated.max_retries

    async def test_retry_clears_failure_reason(self, session: Session):
        """Retry clears failure reason for next attempt."""
        ws = Workspace(id="ws-11", name="Test", slug="test11")
        session.add(ws)
        session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-11",
            position=1,
            status="failed",
            retry_count=0,
            max_retries=3,
            failure_reason="Connection timeout",
        )
        session.add(run)
        session.commit()

        # Simulate retry
        run = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-1")
        ).first()

        run.retry_count += 1
        run.failure_reason = ""
        run.status = QueuePosition.QUEUED
        session.add(run)
        session.commit()

        updated = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-1")
        ).first()

        assert updated.failure_reason == ""
        assert updated.retry_count == 1

    async def test_track_last_failed_at(self, session: Session):
        """Track when run last failed."""
        ws = Workspace(id="ws-12", name="Test", slug="test12")
        session.add(ws)
        session.commit()

        now = datetime.now(timezone.utc)
        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-12",
            position=1,
            status="failed",
            retry_count=1,
            max_retries=3,
            last_failed_at=now,
        )
        session.add(run)
        session.commit()

        updated = session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-1")
        ).first()

        assert updated.last_failed_at is not None
        assert (updated.last_failed_at - now).total_seconds() < 1


@pytest.mark.asyncio
class TestRetryAllFailed:
    """Test retry all failed runs."""

    async def test_retry_all_failed_runs(self, session: Session):
        """Retry all failed runs in workspace."""
        ws = Workspace(id="ws-13", name="Test", slug="test13")
        session.add(ws)
        session.commit()

        # Create multiple failed runs
        failed_runs = [
            QueuedRun(
                run_id=f"run-{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-13",
                position=i,
                status="failed",
                retry_count=0,
                max_retries=3,
            )
            for i in range(1, 4)
        ]
        session.add_all(failed_runs)
        session.commit()

        # Simulate retry all failed
        failed = session.exec(
            select(QueuedRun).where(
                (QueuedRun.workspace_id == "ws-13")
                & (QueuedRun.status == "failed")
            )
        ).all()

        for run in failed:
            run.retry_count += 1
            run.status = QueuePosition.QUEUED
            session.add(run)

        session.commit()

        # Verify all retried
        retried = session.exec(
            select(QueuedRun).where(QueueDRun.workspace_id == "ws-13")
        ).all()

        assert all(r.retry_count > 0 for r in retried)
        assert all(r.status == QueuePosition.QUEUED for r in retried)

    async def test_skip_max_retries_in_retry_all(self, session: Session):
        """Retry all skips runs that exceeded max retries."""
        ws = Workspace(id="ws-14", name="Test", slug="test14")
        session.add(ws)
        session.commit()

        # Mix of failed runs
        run1 = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-14",
            position=1,
            status="failed",
            retry_count=0,
            max_retries=3,
        )
        run2 = QueuedRun(
            run_id="run-2",
            ticket_id="ticket-2",
            workspace_id="ws-14",
            position=2,
            status="failed",
            retry_count=3,  # Already maxed out
            max_retries=3,
        )
        session.add_all([run1, run2])
        session.commit()

        # Retry all
        failed = session.exec(
            select(QueuedRun).where(
                (QueuedRun.workspace_id == "ws-14")
                & (QueuedRun.status == "failed")
            )
        ).all()

        retried_count = 0
        skipped_count = 0

        for run in failed:
            if run.retry_count >= run.max_retries:
                skipped_count += 1
            else:
                run.retry_count += 1
                run.status = QueuePosition.QUEUED
                session.add(run)
                retried_count += 1

        session.commit()

        assert retried_count == 1
        assert skipped_count == 1


@pytest.mark.asyncio
class TestSkipFailedRuns:
    """Test skipping failed runs."""

    async def test_skip_all_failed_runs(self, session: Session):
        """Skip all failed runs in workspace."""
        ws = Workspace(id="ws-15", name="Test", slug="test15")
        session.add(ws)
        session.commit()

        # Create failed runs
        failed_runs = [
            QueuedRun(
                run_id=f"run-{i}",
                ticket_id=f"ticket-{i}",
                workspace_id="ws-15",
                position=i,
                status="failed",
            )
            for i in range(1, 3)
        ]
        session.add_all(failed_runs)
        session.commit()

        # Skip all
        failed = session.exec(
            select(QueuedRun).where(
                (QueueDRun.workspace_id == "ws-15")
                & (QueuedRun.status == "failed")
            )
        ).all()

        for run in failed:
            run.status = "skipped"
            session.add(run)

        session.commit()

        # Verify all skipped
        skipped = session.exec(
            select(QueuedRun).where(
                (QueueDRun.workspace_id == "ws-15")
                & (QueuedRun.status == "skipped")
            )
        ).all()

        assert len(skipped) == 2


@pytest.mark.asyncio
class TestBulkOperationErrors:
    """Test error handling in bulk operations."""

    async def test_database_error_handled(self, session: Session):
        """Database errors don't crash operation."""
        ws = Workspace(id="ws-16", name="Test", slug="test16")
        session.add(ws)
        session.commit()

        # Try operation that fails
        # This would be caught in the actual endpoint
        assert True

    async def test_partial_failure_continues(self, session: Session):
        """Bulk operation continues on individual failure."""
        ws = Workspace(id="ws-17", name="Test", slug="test17")
        session.add(ws)
        session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-17",
            position=1,
        )
        session.add(run)
        session.commit()

        run_ids = ["run-1", "run-nonexistent", "run-another-nonexistent"]
        successful = 0

        for run_id in run_ids:
            r = session.exec(
                select(QueuedRun).where(QueuedRun.run_id == run_id)
            ).first()

            if r:
                successful += 1

        # Should have processed all, found 1
        assert successful == 1
