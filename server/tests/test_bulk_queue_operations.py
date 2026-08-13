"""Bulk queue endpoints address entries, and write the status they claim.

Two defects sat on top of each other. `bulk-cancel` reported every run
"cancelled" while setting QUEUED — which on a live entry re-armed it for
dispatch rather than cancelling anything. And every endpoint keyed on
`QueuedRun.run_id`, which only a shared-queue entry sets; a lane entry names a
ticket and has no run until its lane reaches it. In a real database every entry
is a lane entry, so the whole module matched nothing: the cancel bug was real
but unreachable, and `failed-runs` returned an empty list beside a lane full of
failures.
"""

import pytest
from loregarden.models.domain import QueuedRun, QueuePosition, Ticket, Workspace
from sqlmodel import Session, select


@pytest.fixture(name="workspace")
def workspace_fixture(db_session):
    ws = db_session.exec(select(Workspace)).first()
    assert ws is not None
    return ws


def _entry(
    session: Session,
    workspace,
    code: str,
    *,
    status: QueuePosition = QueuePosition.QUEUED,
    run_id: str | None = None,
    retry_count: int = 0,
) -> QueuedRun:
    ticket = Ticket(external_id=f"T-{code}", workspace_id=workspace.id, title=code)
    session.add(ticket)
    session.commit()
    entry = QueuedRun(
        workspace_id=workspace.id,
        ticket_id=ticket.id,
        run_id=run_id,
        slot_number=1,
        status=status,
        retry_count=retry_count,
    )
    session.add(entry)
    session.commit()
    session.refresh(entry)
    return entry


def _post(client, workspace, path: str, payload):
    return client.post(f"/api/parallel/workspace/{workspace.id}/queue/{path}", json=payload)


# ---- reachability ------------------------------------------------------


def test_a_lane_entry_can_be_cancelled(client, db_session, workspace):
    """The whole module used to miss these: a lane entry has no run_id."""
    entry = _entry(db_session, workspace, "lane", run_id=None)

    body = _post(client, workspace, "bulk-cancel", [entry.id]).json()

    assert body["successful"] == 1
    db_session.refresh(entry)
    assert entry.status == QueuePosition.CANCELLED


def test_failed_entries_are_listed_for_a_lane_entry(client, db_session, workspace):
    entry = _entry(db_session, workspace, "boom", status=QueuePosition.FAILED, run_id=None)

    rows = client.get(f"/api/parallel/workspace/{workspace.id}/queue/failed-entries").json()

    assert [row["entry_id"] for row in rows] == [entry.id]


# ---- cancel ------------------------------------------------------------


def test_cancelling_a_running_entry_is_refused(client, db_session, workspace):
    """Setting QUEUED — what this did — put a live entry back in its lane."""
    entry = _entry(db_session, workspace, "live", status=QueuePosition.ACTIVE)

    body = _post(client, workspace, "bulk-cancel", [entry.id]).json()

    assert body["failed"] == 1
    db_session.refresh(entry)
    assert entry.status == QueuePosition.ACTIVE


def test_a_missing_entry_does_not_stop_the_others(client, db_session, workspace):
    entry = _entry(db_session, workspace, "real")

    body = _post(client, workspace, "bulk-cancel", ["nope", entry.id]).json()

    assert (body["successful"], body["failed"]) == (1, 1)
    db_session.refresh(entry)
    assert entry.status == QueuePosition.CANCELLED


# ---- pause / resume ----------------------------------------------------


def test_pause_takes_an_entry_out_of_its_lane_and_resume_puts_it_back(
    client, db_session, workspace
):
    """Pause is only allowed to exist because resume does.

    PAUSED sits outside WAITING_STATUSES, so the lane will not start a paused
    entry — which without a resume would have been a one-way loss of queued work.
    """
    from loregarden.services.queue_lanes import QueueLaneService

    entry = _entry(db_session, workspace, "hold")
    lanes = QueueLaneService(db_session)

    assert _post(client, workspace, "bulk-pause", [entry.id]).json()["successful"] == 1
    db_session.refresh(entry)
    assert entry.status == QueuePosition.PAUSED
    assert [e.id for e in lanes.waiting_in_lane(1)] == []

    assert _post(client, workspace, "bulk-resume", [entry.id]).json()["successful"] == 1
    db_session.refresh(entry)
    assert entry.status == QueuePosition.QUEUED
    assert [e.id for e in lanes.waiting_in_lane(1)] == [entry.id]


def test_resuming_something_that_is_not_paused_is_refused(client, db_session, workspace):
    entry = _entry(db_session, workspace, "queued")

    assert _post(client, workspace, "bulk-resume", [entry.id]).json()["failed"] == 1


# ---- retry -------------------------------------------------------------


def test_retry_all_counts_only_what_it_re_queued(client, db_session, workspace):
    """`retried` counted every result row, including the skipped ones."""
    retryable = _entry(db_session, workspace, "a", status=QueuePosition.FAILED, retry_count=0)
    exhausted = _entry(db_session, workspace, "b", status=QueuePosition.FAILED, retry_count=3)

    body = _post(client, workspace, "retry-all-failed", None).json()

    assert body["total"] == 2
    assert body["retried"] == 1
    db_session.refresh(retryable)
    db_session.refresh(exhausted)
    assert retryable.status == QueuePosition.QUEUED
    assert exhausted.status == QueuePosition.FAILED


# ---- skip --------------------------------------------------------------


def test_skip_failed_is_a_post_not_a_get(client, db_session, workspace):
    """It writes. A GET that writes is one prefetch away from firing itself."""
    entry = _entry(db_session, workspace, "doomed", status=QueuePosition.FAILED)

    assert (
        client.get(f"/api/parallel/workspace/{workspace.id}/queue/skip-failed").status_code == 405
    )

    body = _post(client, workspace, "skip-failed", None).json()
    assert body["skipped_count"] == 1
    db_session.refresh(entry)
    assert entry.status == QueuePosition.SKIPPED
