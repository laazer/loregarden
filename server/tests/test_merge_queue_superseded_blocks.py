"""A conflict-blocked entry whose PR was queued again stops being reminded.

`_remind_blocked` read every FAILED entry waiting on a conflict ticket, the
ones whose PR had since been queued again included. While the ticket was open
each cycle re-reported a block the queue no longer had; once it closed, each
cycle tried `retry`, which refuses a superseded entry ("queued again"), and
logged that refusal as a cycle error. Seen on 2026-11-28 for PRs #1028/#1035.
"""

from __future__ import annotations

from unittest import mock

import pytest
from loregarden.models.domain import (
    MergeQueueState,
    MergeQueueWait,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services.merge_queue import Candidate, enqueue, transition
from loregarden.services.merge_queue_loop import MergeQueueLoop
from sqlmodel import Session

PR = 8


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    ws = Workspace(slug="mq-blocks", name="mq-blocks", repo_path=str(tmp_path))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


def _ticket(session, workspace, external_id: str) -> Ticket:
    ticket = Ticket(external_id=external_id, workspace_id=workspace.id, title=external_id)
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _failed(session, workspace, ticket, head: str, **fields):
    candidate = Candidate(
        pr_number=PR,
        pr_url=f"https://github.com/o/r/pull/{PR}",
        pr_head_branch="publish/integration/lg-root-1",
        source_branch="integration/lg-root-1",
        head_sha=head,
        base_branch="main",
    )
    entry = enqueue(session, ticket, workspace, candidate).entry
    assert transition(
        session, entry, MergeQueueState.QUEUED, state=MergeQueueState.FAILED, **fields
    )
    session.refresh(entry)
    return entry


@pytest.mark.parametrize("artifact_state", [TicketState.BACKLOG, TicketState.DONE])
def test_a_superseded_conflict_block_is_neither_reminded_nor_retried(
    session, workspace, artifact_state
):
    root = _ticket(session, workspace, "lg-root-1")
    artifact = _ticket(session, workspace, "lg-root-2")
    artifact.state = artifact_state
    session.add(artifact)
    session.commit()
    _failed(
        session,
        workspace,
        root,
        "a" * 40,
        wait=MergeQueueWait.CONFLICT_ARTIFACT_OPEN,
        artifact_ref=artifact.external_id,
        detail="CONFLICT",
    )
    # Queued again past the conflict, and stopped for another reason, so the
    # cycle has no head to stage and runs only the reminder.
    _failed(session, workspace, root, "b" * 40, detail="checks failed")

    report = MergeQueueLoop(session, workspace, github=mock.Mock()).advance()

    assert report.errors == []
    assert not [a for a in report.actions if f"PR #{PR}" in a], report.actions


def test_a_current_conflict_block_is_still_reminded(session, workspace):
    """The control: without a later entry the reminder still fires."""
    root = _ticket(session, workspace, "lg-root-1")
    artifact = _ticket(session, workspace, "lg-root-2")
    _failed(
        session,
        workspace,
        root,
        "a" * 40,
        wait=MergeQueueWait.CONFLICT_ARTIFACT_OPEN,
        artifact_ref=artifact.external_id,
        detail="CONFLICT",
    )

    report = MergeQueueLoop(session, workspace, github=mock.Mock()).advance()

    assert report.actions == [f"PR #{PR}: blocked on {artifact.external_id}"]
