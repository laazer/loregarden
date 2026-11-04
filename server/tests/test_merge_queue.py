"""The merge queue's own state: entries, ordering and admission (lg-milestone-that-968).

No git and no GitHub here; the loop's behaviour is `test_merge_queue_loop`.
"""

from __future__ import annotations

import threading
from unittest import mock

import pytest
from loregarden.db.versions.merge_queue_entries import m_merge_queue_entries
from loregarden.models.domain import (
    MergeQueueAdmission,
    MergeQueueEntry,
    MergeQueueState,
    Ticket,
    Workspace,
)
from loregarden.services import merge_queue
from loregarden.services.merge_queue import (
    STATE_STEP,
    STEPS,
    Candidate,
    QueueRefusal,
    enqueue,
    retry,
    transition,
    withdraw,
)
from sqlalchemy import inspect, text
from sqlmodel import Session, select


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    ws = Workspace(slug="mq", name="mq", repo_path=str(tmp_path))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = Ticket(external_id="lg-1", workspace_id=workspace.id, title="t")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _candidate(head: str, *, pr: int = 7) -> Candidate:
    return Candidate(
        pr_number=pr,
        pr_url=f"https://github.com/o/r/pull/{pr}",
        pr_head_branch="publish/integration/lg-1",
        source_branch="integration/lg-1",
        head_sha=head,
        base_branch="main",
    )


def test_migration_creates_the_table_and_reruns_as_a_no_op(isolated_db):
    with isolated_db.begin() as conn:
        conn.execute(text("DROP TABLE merge_queue_entries"))
    with isolated_db.begin() as conn:
        m_merge_queue_entries(conn)
        m_merge_queue_entries(conn)
    inspector = inspect(isolated_db)
    migrated = {column["name"] for column in inspector.get_columns("merge_queue_entries")}
    assert migrated == set(MergeQueueEntry.model_fields)
    indexes = {index["name"] for index in inspector.get_indexes("merge_queue_entries")}
    assert {"uq_merge_queue_live_head", "ix_merge_queue_entries_state"} <= indexes


def test_state_is_the_enum_not_a_string(session, workspace, ticket):
    admission = enqueue(session, ticket, workspace, _candidate("a" * 40))
    session.expire_all()
    stored = session.get(MergeQueueEntry, admission.entry.id)
    assert stored.state is MergeQueueState.QUEUED


def test_no_pull_request_and_duplicate_head_are_distinct_refusals(session, workspace, ticket):
    no_pr = enqueue(session, ticket, workspace, _candidate("a" * 40, pr=0))
    first = enqueue(session, ticket, workspace, _candidate("b" * 40))
    duplicate = enqueue(session, ticket, workspace, _candidate("b" * 40, pr=8))

    assert no_pr.outcome is MergeQueueAdmission.NO_PULL_REQUEST and not no_pr.admitted
    assert first.outcome is MergeQueueAdmission.ADMITTED
    assert duplicate.outcome is MergeQueueAdmission.DUPLICATE_HEAD and not duplicate.admitted
    assert duplicate.entry.id == first.entry.id
    assert len(session.exec(select(MergeQueueEntry)).all()) == 1


def test_new_work_on_a_queued_pr_keeps_its_place(session, workspace, ticket):
    first = enqueue(session, ticket, workspace, _candidate("a" * 40, pr=7)).entry
    enqueue(session, ticket, workspace, _candidate("c" * 40, pr=9))
    updated = enqueue(session, ticket, workspace, _candidate("b" * 40, pr=7))

    assert updated.outcome is MergeQueueAdmission.UPDATED
    assert updated.entry.id == first.id
    assert updated.entry.head_sha == "b" * 40
    assert [e.pr_number for e in merge_queue.live_entries(session, workspace.id)] == [7, 9]


def test_concurrent_admissions_claim_distinct_positions(isolated_db, workspace, ticket):
    """Every admission reads the same highest position before any inserts.

    The barrier inside `_next_position` holds each thread's first read until
    all of them have read, so every first insert races for one position; the
    unique index refuses all but one and the losers read again. Without that
    index (or with a select-then-insert that ignores the refusal) two rows share
    a position, which is what this asserts against.
    """
    workers = 6
    barrier = threading.Barrier(workers)
    real = merge_queue._next_position
    first_read = threading.local()

    def racing_next_position(session, workspace_id):
        position = real(session, workspace_id)
        if not getattr(first_read, "done", False):
            first_read.done = True
            barrier.wait(timeout=10)
        return position

    results: list[MergeQueueAdmission] = []
    errors: list[BaseException] = []

    def admit(index: int) -> None:
        try:
            with Session(isolated_db) as session:
                ws = session.get(Workspace, workspace.id)
                tk = session.get(Ticket, ticket.id)
                results.append(
                    enqueue(session, tk, ws, _candidate(f"{index:040x}", pr=100 + index)).outcome
                )
        except BaseException as exc:  # noqa: BLE001 — reported by the assertion below
            errors.append(exc)

    with mock.patch.object(merge_queue, "_next_position", racing_next_position):
        threads = [threading.Thread(target=admit, args=(i,)) for i in range(workers)]
        for thread in threads:
            thread.start()
        for thread in threads:
            thread.join(timeout=30)

    assert errors == []
    assert results == [MergeQueueAdmission.ADMITTED] * workers
    with Session(isolated_db) as session:
        positions = [e.position for e in session.exec(select(MergeQueueEntry)).all()]
    assert sorted(positions) == list(range(1, workers + 1))


def test_no_step_that_mutates_shared_state_is_cancellable():
    assert all(not spec.cancellable for spec in STEPS.values() if spec.mutates_shared_state)
    assert {step for step, spec in STEPS.items() if spec.mutates_shared_state} >= {
        STATE_STEP[MergeQueueState.MERGING]
    }


def test_withdraw_follows_the_steps_declaration(session, workspace, ticket):
    testing = enqueue(session, ticket, workspace, _candidate("a" * 40, pr=7)).entry
    merging = enqueue(session, ticket, workspace, _candidate("b" * 40, pr=8)).entry
    assert transition(session, testing, MergeQueueState.QUEUED, state=MergeQueueState.TESTING)
    assert transition(session, merging, MergeQueueState.QUEUED, state=MergeQueueState.MERGING)

    assert withdraw(session, testing.id, reason="operator").state is MergeQueueState.WITHDRAWN
    with pytest.raises(QueueRefusal, match="merging"):
        withdraw(session, merging.id)
    session.refresh(merging)
    assert merging.state is MergeQueueState.MERGING


def test_transition_refuses_an_entry_someone_else_moved(session, workspace, ticket):
    entry = enqueue(session, ticket, workspace, _candidate("a" * 40)).entry
    withdraw(session, entry.id)
    assert not transition(session, entry, MergeQueueState.QUEUED, state=MergeQueueState.STAGING)
    session.refresh(entry)
    assert entry.state is MergeQueueState.WITHDRAWN


def test_retry_sends_a_failed_entry_to_the_back(session, workspace, ticket):
    failed = enqueue(session, ticket, workspace, _candidate("a" * 40, pr=7)).entry
    behind = enqueue(session, ticket, workspace, _candidate("b" * 40, pr=8)).entry
    transition(session, failed, MergeQueueState.QUEUED, state=MergeQueueState.FAILED)

    retried = retry(session, failed.id)

    assert retried.state is MergeQueueState.QUEUED
    assert retried.position > behind.position
    with pytest.raises(QueueRefusal, match="queued"):
        retry(session, behind.id)
