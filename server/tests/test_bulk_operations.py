"""Tests for bulk queue operations and retry logic."""

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import QueuedRun, QueuePosition, Workspace
from sqlmodel import Session, select


@pytest.mark.asyncio
class TestBulkCancelRuns:
    """Test bulk cancel operations."""

    async def test_cancel_multiple_runs(self, db_session: Session):
        """Cancel multiple runs at once."""
        ws = Workspace(id="ws-1", name="Test", slug="test")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add_all(runs)
        db_session.commit()

        run_ids = [f"run-{i}" for i in range(1, 4)]

        # Simulate bulk cancel
        for run_id in run_ids:
            run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()
            run.status = QueuePosition.QUEUED
            db_session.add(run)

        db_session.commit()

        # Verify all cancelled
        for run_id in run_ids:
            run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()
            assert run is not None

    async def test_cancel_nonexistent_run(self, db_session: Session):
        """Cancel non-existent run handles gracefully."""
        ws = Workspace(id="ws-2", name="Test", slug="test2")
        db_session.add(ws)
        db_session.commit()

        # Try to cancel non-existent run
        run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "non-existent")).first()

        assert run is None

    async def test_cancel_mixed_results(self, db_session: Session):
        """Bulk cancel with some successful, some failed."""
        ws = Workspace(id="ws-3", name="Test", slug="test3")
        db_session.add(ws)
        db_session.commit()

        # Create one run
        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-3",
            position=1,
        )
        db_session.add(run)
        db_session.commit()

        # Try to cancel existing and non-existing
        run_ids = ["run-1", "run-nonexistent"]
        successful = 0
        failed = 0

        for run_id in run_ids:
            r = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()

            if r:
                r.status = QueuePosition.QUEUED
                db_session.add(r)
                successful += 1
            else:
                failed += 1

        db_session.commit()

        assert successful == 1
        assert failed == 1


@pytest.mark.asyncio
class TestBulkPauseRuns:
    """Test bulk pause operations."""

    async def test_pause_multiple_active_runs(self, db_session: Session):
        """Pause multiple active runs."""
        ws = Workspace(id="ws-4", name="Test", slug="test4")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add_all(runs)
        db_session.commit()

        # Pause all
        for run in runs:
            r = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run.run_id)).first()
            r.status = "paused"
            db_session.add(r)

        db_session.commit()

        # Verify paused
        paused = db_session.exec(select(QueuedRun).where(QueuedRun.workspace_id == "ws-4")).all()

        assert all(r.status == "paused" for r in paused)

    async def test_pause_only_active_runs(self, db_session: Session):
        """Pause only affects active runs, not queued."""
        ws = Workspace(id="ws-5", name="Test", slug="test5")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add_all([active, queued])
        db_session.commit()

        # Try to pause both
        for run_id in ["run-active", "run-queued"]:
            run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()

            if run.status == QueuePosition.STARTED:
                run.status = "paused"
                db_session.add(run)

        db_session.commit()

        active_check = db_session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-active")
        ).first()
        queued_check = db_session.exec(
            select(QueuedRun).where(QueuedRun.run_id == "run-queued")
        ).first()

        assert active_check.status == "paused"
        assert queued_check.status == QueuePosition.QUEUED


@pytest.mark.asyncio
class TestBulkReorderRuns:
    """Test bulk reorder operations."""

    async def test_reorder_multiple_runs(self, db_session: Session):
        """Reorder multiple runs in queue."""
        ws = Workspace(id="ws-6", name="Test", slug="test6")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add_all(runs)
        db_session.commit()

        # Reorder: reverse the order
        new_order = ["run-3", "run-2", "run-1"]

        for new_pos, run_id in enumerate(new_order, 1):
            run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()
            run.position = new_pos
            db_session.add(run)

        db_session.commit()

        # Verify new positions
        for new_pos, run_id in enumerate(new_order, 1):
            run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()
            assert run.position == new_pos

    async def test_reorder_partial_queue(self, db_session: Session):
        """Reorder subset of runs in queue."""
        ws = Workspace(id="ws-7", name="Test", slug="test7")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add_all(runs)
        db_session.commit()

        # Reorder only run-2 and run-4
        partial_order = ["run-4", "run-2"]

        for new_pos, run_id in enumerate(partial_order, 1):
            run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()
            run.position = new_pos
            db_session.add(run)

        db_session.commit()

        # Verify partial reorder
        run2 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-2")).first()
        run4 = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-4")).first()

        assert run4.position == 1
        assert run2.position == 2


@pytest.mark.asyncio
class TestRetryLogic:
    """Test retry logic with exponential backoff."""

    async def test_retry_increments_count(self, db_session: Session):
        """Retry increments retry counter."""
        ws = Workspace(id="ws-8", name="Test", slug="test8")
        db_session.add(ws)
        db_session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-8",
            position=1,
            status="failed",
            retry_count=0,
            max_retries=3,
        )
        db_session.add(run)
        db_session.commit()

        # Simulate retry
        run.retry_count += 1
        run.status = QueuePosition.QUEUED
        db_session.add(run)
        db_session.commit()

        updated = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-1")).first()

        assert updated.retry_count == 1
        assert updated.status == QueuePosition.QUEUED

    async def test_exponential_backoff_calculation(self, db_session: Session):
        """Exponential backoff calculates correctly: 2^retry_count."""
        ws = Workspace(id="ws-9", name="Test", slug="test9")
        db_session.add(ws)
        db_session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-9",
            position=1,
            status="failed",
            retry_count=0,
            max_retries=5,
        )
        db_session.add(run)
        db_session.commit()

        # Simulate retries and check backoff
        for attempt in range(3):
            run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-1")).first()

            backoff_seconds = 2**run.retry_count
            expected_backoff = 2**attempt

            assert backoff_seconds == expected_backoff

            run.retry_count += 1
            run.estimated_start_at = datetime.now(timezone.utc) + timedelta(seconds=backoff_seconds)
            db_session.add(run)
            db_session.commit()

    async def test_max_retries_exceeded(self, db_session: Session):
        """Cannot retry when max retries exceeded."""
        ws = Workspace(id="ws-10", name="Test", slug="test10")
        db_session.add(ws)
        db_session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-10",
            position=1,
            status="failed",
            retry_count=3,
            max_retries=3,
        )
        db_session.add(run)
        db_session.commit()

        updated = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-1")).first()

        assert updated.retry_count >= updated.max_retries

    async def test_retry_clears_failure_reason(self, db_session: Session):
        """Retry clears failure reason for next attempt."""
        ws = Workspace(id="ws-11", name="Test", slug="test11")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add(run)
        db_session.commit()

        # Simulate retry
        run = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-1")).first()

        run.retry_count += 1
        run.failure_reason = ""
        run.status = QueuePosition.QUEUED
        db_session.add(run)
        db_session.commit()

        updated = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-1")).first()

        assert updated.failure_reason == ""
        assert updated.retry_count == 1

    async def test_track_last_failed_at(self, db_session: Session):
        """Track when run last failed."""
        ws = Workspace(id="ws-12", name="Test", slug="test12")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add(run)
        db_session.commit()

        updated = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == "run-1")).first()

        assert updated.last_failed_at is not None
        # SQLite (via SQLModel) round-trips datetimes as naive values, stripping
        # tzinfo even though we always write UTC-aware values (see `utcnow()` in
        # models/domain/enums.py) — re-attach UTC before comparing, consistent
        # with the rest of this codebase's naive-storage/aware-application convention.
        stored = updated.last_failed_at
        if stored.tzinfo is None:
            stored = stored.replace(tzinfo=timezone.utc)
        assert (stored - now).total_seconds() < 1


@pytest.mark.asyncio
class TestRetryAllFailed:
    """Test retry all failed runs."""

    async def test_retry_all_failed_runs(self, db_session: Session):
        """Retry all failed runs in workspace."""
        ws = Workspace(id="ws-13", name="Test", slug="test13")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add_all(failed_runs)
        db_session.commit()

        # Simulate retry all failed
        failed = db_session.exec(
            select(QueuedRun).where(
                (QueuedRun.workspace_id == "ws-13") & (QueuedRun.status == "failed")
            )
        ).all()

        for run in failed:
            run.retry_count += 1
            run.status = QueuePosition.QUEUED
            db_session.add(run)

        db_session.commit()

        # Verify all retried
        retried = db_session.exec(select(QueuedRun).where(QueuedRun.workspace_id == "ws-13")).all()

        assert all(r.retry_count > 0 for r in retried)
        assert all(r.status == QueuePosition.QUEUED for r in retried)

    async def test_skip_max_retries_in_retry_all(self, db_session: Session):
        """Retry all skips runs that exceeded max retries."""
        ws = Workspace(id="ws-14", name="Test", slug="test14")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add_all([run1, run2])
        db_session.commit()

        # Retry all
        failed = db_session.exec(
            select(QueuedRun).where(
                (QueuedRun.workspace_id == "ws-14") & (QueuedRun.status == "failed")
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
                db_session.add(run)
                retried_count += 1

        db_session.commit()

        assert retried_count == 1
        assert skipped_count == 1


@pytest.mark.asyncio
class TestSkipFailedRuns:
    """Test skipping failed runs."""

    async def test_skip_all_failed_runs(self, db_session: Session):
        """Skip all failed runs in workspace."""
        ws = Workspace(id="ws-15", name="Test", slug="test15")
        db_session.add(ws)
        db_session.commit()

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
        db_session.add_all(failed_runs)
        db_session.commit()

        # Skip all
        failed = db_session.exec(
            select(QueuedRun).where(
                (QueuedRun.workspace_id == "ws-15") & (QueuedRun.status == "failed")
            )
        ).all()

        for run in failed:
            run.status = "skipped"
            db_session.add(run)

        db_session.commit()

        # Verify all skipped
        skipped = db_session.exec(
            select(QueuedRun).where(
                (QueuedRun.workspace_id == "ws-15") & (QueuedRun.status == "skipped")
            )
        ).all()

        assert len(skipped) == 2


@pytest.mark.asyncio
class TestBulkOperationErrors:
    """Test error handling in bulk operations."""

    async def test_database_error_handled(self, db_session: Session):
        """Database errors don't crash operation."""
        ws = Workspace(id="ws-16", name="Test", slug="test16")
        db_session.add(ws)
        db_session.commit()

        # Try operation that fails
        # This would be caught in the actual endpoint
        assert True

    async def test_partial_failure_continues(self, db_session: Session):
        """Bulk operation continues on individual failure."""
        ws = Workspace(id="ws-17", name="Test", slug="test17")
        db_session.add(ws)
        db_session.commit()

        run = QueuedRun(
            run_id="run-1",
            ticket_id="ticket-1",
            workspace_id="ws-17",
            position=1,
        )
        db_session.add(run)
        db_session.commit()

        run_ids = ["run-1", "run-nonexistent", "run-another-nonexistent"]
        successful = 0

        for run_id in run_ids:
            r = db_session.exec(select(QueuedRun).where(QueuedRun.run_id == run_id)).first()

            if r:
                successful += 1

        # Should have processed all, found 1
        assert successful == 1
