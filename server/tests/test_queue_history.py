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
from loregarden.services.queue_history import MAX_ATTENTION_PER_LANE, QueueHistoryService
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


def test_direct_admission_without_a_lane_entry_still_appears_in_history(session, workspace):
    """reserve+bind used to skip QueuedRun — success must still show on the board."""
    ticket = Ticket(
        external_id="t-direct",
        workspace_id=workspace.id,
        title="Direct admit",
    )
    session.add(ticket)
    session.commit()
    orch = OrchestrationRun(
        run_code="orch_direct",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.SUCCEEDED,
        finished_at=START + timedelta(minutes=5),
        started_at=START,
    )
    session.add(orch)
    session.commit()

    entries, total = QueueHistoryService(session).list_history(workspace_id=workspace.id)

    assert total == 1
    assert entries[0].outcome == "succeeded"
    assert entries[0].ticket_external_id == "t-direct"
    assert entries[0].orchestration_run_id == orch.id


def test_nested_child_orch_under_parent_is_not_synthesized(session, workspace):
    parent = Ticket(external_id="t-parent", workspace_id=workspace.id, title="Parent")
    session.add(parent)
    session.commit()
    child = Ticket(
        external_id="t-child",
        workspace_id=workspace.id,
        title="Child",
        parent_ticket_id=parent.id,
    )
    session.add(child)
    session.commit()

    parent_orch = OrchestrationRun(
        run_code="orch_parent",
        ticket_id=parent.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.SUCCEEDED,
        started_at=START,
        finished_at=START + timedelta(minutes=10),
    )
    child_orch = OrchestrationRun(
        run_code="orch_child",
        ticket_id=child.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.SUCCEEDED,
        started_at=START + timedelta(minutes=1),
        finished_at=START + timedelta(minutes=5),
    )
    session.add(parent_orch)
    session.add(child_orch)
    # Parent went through a lane; child was nested execute.
    session.add(
        QueuedRun(
            workspace_id=workspace.id,
            ticket_id=parent.id,
            orchestration_run_id=parent_orch.id,
            slot_number=1,
            status=QueuePosition.STARTED,
            started_at=START,
        )
    )
    session.commit()

    entries, _ = QueueHistoryService(session).list_history(workspace_id=workspace.id)
    codes = {entry.ticket_external_id for entry in entries}
    assert codes == {"t-parent"}


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


def test_lane_attention_holds_only_undismissed_blocked_and_failed(session, workspace):
    """A lane keeps what stopped in it — and nothing else, whatever the outcome."""
    blocked = _entry(
        session,
        workspace,
        code="t-blocked",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.BLOCKED,
        slot_number=1,
        minutes_ago=1,
    )
    failed = _entry(
        session,
        workspace,
        code="t-failed",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.FAILED,
        slot_number=1,
        minutes_ago=5,
    )
    _entry(
        session,
        workspace,
        code="t-ok",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.SUCCEEDED,
        slot_number=1,
    )
    _entry(
        session,
        workspace,
        code="t-cancelled",
        status=QueuePosition.CANCELLED,
        orchestration_status=OrchestrationRunStatus.FAILED,
        slot_number=1,
    )
    _entry(
        session,
        workspace,
        code="t-other-lane",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.FAILED,
        slot_number=2,
    )

    attention = QueueHistoryService(session).lane_attention()

    cards, total = attention[1]
    assert total == 2
    # Newest first, so the most recent failure is not buried under older ones.
    assert [card.entry_id for card in cards] == [blocked.id, failed.id]
    assert [card.outcome for card in cards] == ["blocked", "failed"]
    assert [card.ticket_external_id for card in attention[2][0]] == ["t-other-lane"]


def test_dismissing_clears_a_card_from_its_lane_but_not_from_history(session, workspace):
    entry = _entry(
        session,
        workspace,
        code="t-failed",
        status=QueuePosition.STARTED,
        orchestration_status=OrchestrationRunStatus.FAILED,
        slot_number=1,
    )
    service = QueueHistoryService(session)

    assert service.dismiss_entry(entry.id) is True

    assert service.lane_attention() == {}
    entries, total = service.list_history(workspace_id=workspace.id)
    assert total == 1
    assert entries[0].entry_id == entry.id


def test_a_live_entry_cannot_be_dismissed(session, workspace):
    """Dismissal is an acknowledgement of a finished entry, not a way to hide one."""
    entry = _entry(
        session,
        workspace,
        code="t-running",
        status=QueuePosition.ACTIVE,
        orchestration_status=OrchestrationRunStatus.RUNNING,
    )

    assert QueueHistoryService(session).dismiss_entry(entry.id) is False
    session.refresh(entry)
    assert entry.dismissed_at is None


def test_lane_attention_caps_cards_but_not_the_count(session, workspace):
    """The websocket carries this every few seconds; the tail travels as a number."""
    for index in range(MAX_ATTENTION_PER_LANE + 3):
        _entry(
            session,
            workspace,
            code=f"t-fail-{index}",
            status=QueuePosition.STARTED,
            orchestration_status=OrchestrationRunStatus.FAILED,
            slot_number=1,
            minutes_ago=index,
        )

    cards, total = QueueHistoryService(session).lane_attention()[1]

    assert len(cards) == MAX_ATTENTION_PER_LANE
    assert total == MAX_ATTENTION_PER_LANE + 3
