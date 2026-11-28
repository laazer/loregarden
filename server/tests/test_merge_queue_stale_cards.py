"""A merge-queue "stopped" card closes once its PR is queued again (lg-milestone-that-2821).

The queue hid a stopped entry as soon as its PR was queued again, but the
card it filed stayed open, asking for a decision already made, until the PR
merged.
"""

from __future__ import annotations

import ast
from pathlib import Path

import pytest
from loregarden.models.domain import (
    Approval,
    ApprovalStatus,
    MergeQueueAdmission,
    MergeQueueState,
    Ticket,
    Workspace,
)
from loregarden.services.invariant_registry import INVARIANTS
from loregarden.services.merge_queue import Candidate, enqueue, retry, transition
from loregarden.services.merge_queue_reports import RESOLVED_BY_QUEUE, report_stopped
from loregarden.services.merge_queue_stale_cards import STOPPED_CARDS_CLOSE_WHEN_QUEUED_AGAIN
from sqlmodel import Session

SERVICES = Path(__file__).resolve().parents[1] / "loregarden" / "services"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    ws = Workspace(slug="mq-cards", name="mq-cards", repo_path=str(tmp_path))
    session.add(ws)
    session.commit()
    session.refresh(ws)
    return ws


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    ticket = Ticket(external_id="lg-cards-1", workspace_id=workspace.id, title="t")
    session.add(ticket)
    session.commit()
    session.refresh(ticket)
    return ticket


def _candidate(head: str, *, pr: int = 7) -> Candidate:
    return Candidate(
        pr_number=pr,
        pr_url=f"https://github.com/o/r/pull/{pr}",
        pr_head_branch="publish/integration/lg-cards-1",
        source_branch="integration/lg-cards-1",
        head_sha=head,
        base_branch="main",
    )


def _stopped(session, workspace, ticket, head: str, *, pr: int = 7):
    """An admitted entry that failed and filed its card."""
    entry = enqueue(session, ticket, workspace, _candidate(head, pr=pr)).entry
    assert transition(
        session, entry, MergeQueueState.QUEUED, state=MergeQueueState.FAILED, detail="checks failed"
    )
    session.refresh(entry)
    card = report_stopped(session, entry)
    assert card.status is ApprovalStatus.PENDING
    return entry, card


def _status(session, card: Approval) -> ApprovalStatus:
    session.refresh(card)
    return card.status


def test_retrying_a_stopped_entry_closes_its_card(session, workspace, ticket):
    entry, card = _stopped(session, workspace, ticket, "a" * 40)

    retry(session, entry.id)

    assert _status(session, card) is ApprovalStatus.APPROVED
    assert card.resolved_by == RESOLVED_BY_QUEUE


def test_enqueueing_a_stopped_pr_again_closes_its_card(session, workspace, ticket):
    _, card = _stopped(session, workspace, ticket, "a" * 40)

    admission = enqueue(session, ticket, workspace, _candidate("b" * 40))

    assert admission.outcome is MergeQueueAdmission.ADMITTED
    assert _status(session, card) is ApprovalStatus.APPROVED


def test_another_prs_card_stays_open(session, workspace, ticket):
    _, other = _stopped(session, workspace, ticket, "a" * 40, pr=8)

    enqueue(session, ticket, workspace, _candidate("b" * 40, pr=7))

    assert _status(session, other) is ApprovalStatus.PENDING


def test_the_invariant_closes_a_card_its_pr_was_queued_past(session, workspace, ticket):
    """The backlog: a card left open by an admission from before the writers closed it."""
    entry, card = _stopped(session, workspace, ticket, "a" * 40)
    _, current = _stopped(session, workspace, ticket, "c" * 40, pr=9)
    # Queued again behind the writers' backs, as before the fix.
    assert transition(
        session, entry, MergeQueueState.FAILED, state=MergeQueueState.QUEUED, detail="again"
    )

    violations = STOPPED_CARDS_CLOSE_WHEN_QUEUED_AGAIN.detect(session)
    assert [v.subject_id for v in violations] == [card.id]

    assert STOPPED_CARDS_CLOSE_WHEN_QUEUED_AGAIN.repair(session, violations) == 1
    assert _status(session, card) is ApprovalStatus.APPROVED
    assert _status(session, current) is ApprovalStatus.PENDING, "PR #9 is still stopped"
    assert STOPPED_CARDS_CLOSE_WHEN_QUEUED_AGAIN.detect(session) == []
    assert STOPPED_CARDS_CLOSE_WHEN_QUEUED_AGAIN in INVARIANTS


def _calls(fn: ast.AST) -> set[str]:
    names: set[str] = set()
    for node in ast.walk(fn):
        if type(node) is ast.Call:
            func = node.func
            names.add(func.id if type(func) is ast.Name else getattr(func, "attr", ""))
    return names


def test_every_writer_that_queues_a_pr_again_closes_its_stopped_card():
    """Enumerates the writers that put a PR in line, so a new one is read, not assumed.

    A new entry is built by `MergeQueueEntry(...)`; a stopped one goes back in
    line only through `retry` (`_take_new_head` moves a live entry, which has
    no card). Each must close the PR's stopped card.
    """
    writers: dict[str, set[str]] = {}
    for path in sorted(SERVICES.glob("merge_queue*.py")):
        tree = ast.parse(path.read_text(encoding="utf-8"))
        for fn in ast.walk(tree):
            if type(fn) is not ast.FunctionDef:
                continue
            called = _calls(fn)
            if "MergeQueueEntry" in called or fn.name == "retry":
                writers[f"{path.name}::{fn.name}"] = called
    assert set(writers) == {"merge_queue.py::_insert", "merge_queue.py::retry"}, (
        "a new writer puts a PR in line: make it close the stopped card, then add it here"
    )
    assert all("close_card" in called for called in writers.values())
