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
from loregarden.models.domain import (
    OrchestrationRunStatus,
    QueuedRun,
    QueuePosition,
    RunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.queue_invariants import WAITING_ENTRIES_HAVE_NO_LIVE_WORK
from loregarden.testing.factories import make_agent_run, make_orchestration_run
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
    max_retries: int = 3,
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
        max_retries=max_retries,
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


def test_a_retried_entry_rejoins_the_back_of_its_own_lane(client, db_session, workspace):
    """It keeps its lane and loses its stale position.

    The lane kept running while this entry sat FAILED, so the position it failed
    at is now held by someone else. `waiting_in_lane` orders by that column, so
    the retried entry jumped ahead of an entry that had been waiting since — and
    the lane read 1, 1 on the board.
    """
    from loregarden.services.queue_lanes import QueueLaneService

    failed = _entry(db_session, workspace, "boom", status=QueuePosition.FAILED)
    waiting = _entry(db_session, workspace, "next")
    lanes = QueueLaneService(db_session)
    assert [e.id for e in lanes.waiting_in_lane(1)] == [waiting.id]

    assert _post(client, workspace, f"{failed.id}/retry", None).status_code == 200

    db_session.refresh(failed)
    db_session.refresh(waiting)
    assert failed.slot_number == 1
    assert [e.id for e in lanes.waiting_in_lane(1)] == [waiting.id, failed.id]
    assert [e.position for e in lanes.waiting_in_lane(1)] == [1, 2]


def test_each_retry_is_counted_and_doubles_the_reported_backoff(client, db_session, workspace):
    entry = _entry(
        db_session, workspace, "again", status=QueuePosition.FAILED, retry_count=2, max_retries=5
    )

    body = _post(client, workspace, f"{entry.id}/retry", None).json()

    assert (body["retry_count"], body["backoff_seconds"]) == (3, 4)
    db_session.refresh(entry)
    assert (entry.status, entry.retry_count) == (QueuePosition.QUEUED, 3)


def test_retrying_an_entry_out_of_retries_is_refused(client, db_session, workspace):
    entry = _entry(
        db_session, workspace, "spent", status=QueuePosition.FAILED, retry_count=3, max_retries=3
    )

    assert _post(client, workspace, f"{entry.id}/retry", None).status_code == 400

    db_session.refresh(entry)
    assert (entry.status, entry.retry_count) == (QueuePosition.FAILED, 3)


@pytest.mark.parametrize("status", [s for s in QueuePosition if s is not QueuePosition.FAILED])
def test_only_a_failed_entry_can_be_retried(client, db_session, workspace, status):
    """Retry never looked at the status, so on a live entry it wrote QUEUED over
    running work, and the lane would start it a second time."""
    entry = _entry(db_session, workspace, "notfailed", status=status)

    assert _post(client, workspace, f"{entry.id}/retry", None).status_code == 409

    db_session.refresh(entry)
    assert (entry.status, entry.retry_count) == (status, 0)


def test_retrying_a_missing_entry_is_404(client, workspace):
    assert _post(client, workspace, "nope/retry", None).status_code == 404


# ---- workspace isolation -----------------------------------------------


@pytest.mark.parametrize("path", ["bulk-cancel", "bulk-pause"])
def test_another_workspaces_entry_is_not_found(client, db_session, workspace, path):
    other = Workspace(slug="bulk-other", name="Other")
    db_session.add(other)
    db_session.commit()
    entry = _entry(db_session, other, "theirs")

    body = _post(client, workspace, path, [entry.id]).json()

    assert (body["successful"], body["failed"]) == (0, 1)
    db_session.refresh(entry)
    assert entry.status == QueuePosition.QUEUED


# ---- reorder -----------------------------------------------------------


def test_reorder_numbers_entries_in_the_order_given(client, db_session, workspace):
    first, second, third = (_entry(db_session, workspace, code) for code in ("a", "b", "c"))

    body = _post(client, workspace, "bulk-reorder", [third.id, first.id, second.id]).json()

    assert (body["successful"], body["failed"]) == (3, 0)
    for entry in (first, second, third):
        db_session.refresh(entry)
    assert (third.position, first.position, second.position) == (1, 2, 3)


def test_a_reorder_naming_a_missing_entry_moves_nothing(client, db_session, workspace):
    first, second = (_entry(db_session, workspace, code) for code in ("a", "b"))
    before = (first.position, second.position)

    body = _post(client, workspace, "bulk-reorder", [second.id, "nope", first.id]).json()

    assert body["successful"] == 0
    assert [row["entry_id"] for row in body["results"]] == ["nope"]
    db_session.refresh(first)
    db_session.refresh(second)
    assert (first.position, second.position) == before


# ---- the invariant over what retry used to write -----------------------


@pytest.mark.parametrize(
    "run_status, violates",
    [
        (RunStatus.RUNNING, True),
        (RunStatus.AWAITING_PERMISSION, True),
        (RunStatus.QUEUED, False),
        (RunStatus.FAILED, False),
    ],
)
def test_a_waiting_entry_whose_run_is_live_is_put_back_to_active(
    db_session, workspace, run_status, violates
):
    entry = _entry(db_session, workspace, "live-run")
    entry.run_id = make_agent_run(
        db_session, workspace_id=workspace.id, ticket_id=entry.ticket_id, status=run_status
    ).id
    db_session.add(entry)
    db_session.commit()

    violations = WAITING_ENTRIES_HAVE_NO_LIVE_WORK.detect(db_session)
    assert [v.subject_id for v in violations] == ([entry.id] if violates else [])

    assert WAITING_ENTRIES_HAVE_NO_LIVE_WORK.repair(db_session, violations) == int(violates)
    db_session.refresh(entry)
    assert entry.status is (QueuePosition.ACTIVE if violates else QueuePosition.QUEUED)
    assert WAITING_ENTRIES_HAVE_NO_LIVE_WORK.detect(db_session) == []


@pytest.mark.parametrize(
    "orch_status, violates",
    [(OrchestrationRunStatus.RUNNING, True), (OrchestrationRunStatus.FAILED, False)],
)
def test_the_same_holds_for_an_entry_naming_an_orchestration(
    db_session, workspace, orch_status, violates
):
    entry = _entry(db_session, workspace, "live-orch")
    orch = make_orchestration_run(db_session, workspace_id=workspace.id, ticket_id=entry.ticket_id)
    orch.status = orch_status
    db_session.add(orch)
    entry.orchestration_run_id = orch.id
    db_session.add(entry)
    db_session.commit()

    violations = WAITING_ENTRIES_HAVE_NO_LIVE_WORK.detect(db_session)
    assert [v.subject_id for v in violations] == ([entry.id] if violates else [])


def test_the_repair_leaves_an_entry_whose_run_ended_after_detection(db_session, workspace):
    entry = _entry(db_session, workspace, "ended")
    run = make_agent_run(
        db_session,
        workspace_id=workspace.id,
        ticket_id=entry.ticket_id,
        status=RunStatus.RUNNING,
    )
    entry.run_id = run.id
    db_session.add(entry)
    db_session.commit()
    violations = WAITING_ENTRIES_HAVE_NO_LIVE_WORK.detect(db_session)
    run.status = RunStatus.FAILED
    db_session.add(run)
    db_session.commit()

    assert WAITING_ENTRIES_HAVE_NO_LIVE_WORK.repair(db_session, violations) == 0
    db_session.refresh(entry)
    assert entry.status is QueuePosition.QUEUED
