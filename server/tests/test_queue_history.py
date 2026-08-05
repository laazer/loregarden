"""Finished lane entries stay readable after their lane releases.

The behaviour under test is the outcome derivation: `QueuePosition.STARTED` is
the terminal "lane released" state, not a running one, so a card built from the
entry's own status would call every finished ticket "started". The orchestration
run is what knows whether the ticket succeeded, blocked or failed.
"""

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    Ticket,
    Workspace,
)
from loregarden.services.queue_history import QueueHistoryService
from sqlmodel import Session

START = datetime(2026, 8, 5, 12, 0, tzinfo=timezone.utc)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session):
    ws = Workspace(slug="proj", name="proj", repo_path=".")
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _entry(
    session: Session,
    workspace: Workspace,
    *,
    code: str,
    status: QueuePosition,
    orchestration_status: OrchestrationRunStatus | None,
    slot_number: int = 1,
    error_message: str = "",
    stage_key: str = "",
    minutes_ago: int = 0,
) -> QueuedRun:
    ticket = Ticket(external_id=code, workspace_id=workspace.id, title=f"Ticket {code}")
    session.add(ticket)
    session.commit()

    orchestration = None
    if orchestration_status is not None:
        orchestration = OrchestrationRun(
            run_code=f"orch_{code}",
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            status=orchestration_status,
            current_stage_key=stage_key,
            error_message=error_message,
            finished_at=START + timedelta(minutes=5),
        )
        session.add(orchestration)
        session.commit()

    entry = QueuedRun(
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        orchestration_run_id=orchestration.id if orchestration else None,
        slot_number=slot_number,
        status=status,
        started_at=START - timedelta(minutes=minutes_ago),
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def test_released_lane_entry_reports_the_orchestration_outcome(session, workspace):
    """STARTED means the lane released, so the card must say "blocked", not "started"."""
    _entry(
        session,
        workspace,
        code="t-blocked",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.BLOCKED,
        stage_key="test_design",
        error_message="Jailed creature definition persistence",
    )

    entries, total = QueueHistoryService(session).list_history(workspace_id=workspace.id)

    assert total == 1
    card = entries[0]
    assert card.status == "started"
    assert card.outcome == "blocked"
    assert card.last_stage_key == "test_design"
    assert card.failure_reason == "Jailed creature definition persistence"
    assert card.ticket_external_id == "t-blocked"
    assert card.duration_seconds == 5 * 60


def test_live_entries_are_not_history(session, workspace):
    for status in (
        QueuePosition.QUEUED,
        QueuePosition.SCHEDULED,
        QueuePosition.PROMOTED,
        QueuePosition.ACTIVE,
    ):
        _entry(
            session,
            workspace,
            code=f"t-{status.value}",
            status=status,
            orchestration_status=OrchestrationRunStatus.RUNNING,
        )

    entries, total = QueueHistoryService(session).list_history(workspace_id=workspace.id)

    assert (entries, total) == ([], 0)


def test_entry_without_an_orchestration_is_unknown_not_succeeded(session, workspace):
    """Removed-before-dispatch or restart-stranded: nothing answers for the ticket."""
    _entry(
        session,
        workspace,
        code="t-orphan",
        status=QueuePosition.STARTED,
        orchestration_status=None,
    )

    entries, _ = QueueHistoryService(session).list_history(workspace_id=workspace.id)

    assert entries[0].outcome == "unknown"
    assert entries[0].duration_seconds is None


def test_cancelled_entry_outranks_its_orchestration(session, workspace):
    """The queue cancelling an entry is its own decision, not the pipeline's."""
    _entry(
        session,
        workspace,
        code="t-cancelled",
        status=QueuePosition.CANCELLED,
        orchestration_status=OrchestrationRunStatus.RUNNING,
    )

    entries, _ = QueueHistoryService(session).list_history(workspace_id=workspace.id)

    assert entries[0].outcome == "cancelled"


def test_filters_and_paging(session, workspace):
    _entry(
        session,
        workspace,
        code="t-ok",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.SUCCEEDED,
        slot_number=1,
        minutes_ago=0,
    )
    _entry(
        session,
        workspace,
        code="t-fail",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.FAILED,
        slot_number=2,
        minutes_ago=10,
    )
    service = QueueHistoryService(session)

    newest_first, total = service.list_history(workspace_id=workspace.id)
    assert total == 2
    assert [card.ticket_external_id for card in newest_first] == ["t-ok", "t-fail"]

    by_outcome, total = service.list_history(workspace_id=workspace.id, outcome="failed")
    assert total == 1
    assert by_outcome[0].ticket_external_id == "t-fail"

    by_slot, _ = service.list_history(workspace_id=workspace.id, slot_number=2)
    assert [card.ticket_external_id for card in by_slot] == ["t-fail"]

    page_two, total = service.list_history(workspace_id=workspace.id, limit=1, offset=1)
    assert total == 2
    assert [card.ticket_external_id for card in page_two] == ["t-fail"]


def test_other_workspaces_are_excluded(session, workspace):
    other = Workspace(slug="other", name="other", repo_path=".")
    session.add(other)
    session.commit()
    _entry(
        session,
        other,
        code="t-other",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.SUCCEEDED,
    )

    entries, total = QueueHistoryService(session).list_history(workspace_id=workspace.id)

    assert (entries, total) == ([], 0)
