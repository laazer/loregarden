"""A pull request admitted by number lands with its own words and closes the tickets it names.

Such an entry has no ticket. The staged commit used to be named "PR #N", so
GitHub's one-commit squash put that on main in place of the PR's title and
body, and the ``Ticket:`` line naming the work went with it. Nothing closed that
ticket either. Same harness as `test_merge_queue_pull_requests`.
"""

from __future__ import annotations

import json

from loregarden.models.domain import (
    DomainEvent,
    EventType,
    MergeQueueState,
    OrchestrationRun,
    OrchestrationRunStatus,
    TicketState,
)
from loregarden.services.merge_queue_landed_tickets import named_tickets
from loregarden.services.merge_queue_reports import RESOLVED_BY_QUEUE
from sqlmodel import select
from tests.test_merge_queue_loop import (  # noqa: F401 - fixtures by name
    _cycle,
    _ticket,
    git,
    github_fixture,
    origin_fixture,
    repo_fixture,
    session_fixture,
    workspace_fixture,
)
from tests.test_merge_queue_pull_requests import (  # noqa: F401 - fixtures by name
    BRANCH,
    PR,
    _enqueue,
    authors_fixture,
    opened_fixture,
)


def _land(session, workspace, authors):
    """Stage, pass and merge the PR; the entry and the landing cycle's report."""
    entry = _enqueue(session, workspace, authors)
    _cycle(session, workspace, authors)
    authors.pass_everything = True
    report = _cycle(session, workspace, authors)
    session.refresh(entry)
    assert entry.state is MergeQueueState.MERGED, entry.detail
    return entry, report


def _state_events(session, ticket) -> list[dict]:
    rows = session.exec(
        select(DomainEvent).where(
            DomainEvent.ticket_id == ticket.id,
            DomainEvent.type == EventType.TICKET_STATE_CHANGED,
        )
    ).all()
    return [json.loads(row.payload_json) for row in rows]


def test_the_staged_commit_carries_the_prs_title_and_body(session, workspace, authors, opened):
    authors.title = "fix(queue): land with the author's words"
    authors.body = "## Summary\nWhy it changed.\n\nTicket: lg-landed-7."
    entry = _enqueue(session, workspace, authors)

    _cycle(session, workspace, authors)
    session.refresh(entry)

    assert entry.state is MergeQueueState.TESTING, entry.detail
    message = git(authors.origin, "log", "-1", "--format=%B", entry.staged_sha).strip()
    assert message == f"{authors.title}\n\n{authors.body}"


def test_a_pr_that_cannot_be_read_before_staging_waits_and_counts_the_attempt(
    session, workspace, authors, opened
):
    entry = _enqueue(session, workspace, authors)
    authors.unreachable = True

    report = _cycle(session, workspace, authors)
    session.refresh(entry)

    assert entry.state is MergeQueueState.QUEUED
    assert entry.attempts == 1
    assert "could not read PR" in entry.detail
    assert git(authors.origin, "rev-parse", f"refs/heads/{BRANCH}") == opened, "it staged anyway"
    assert any(f"PR #{PR}" in error for error in report.errors)


def test_a_landed_pr_closes_the_tickets_its_body_names(session, workspace, authors, opened):
    named = _ticket(session, workspace, "lg-landed-7")
    other = _ticket(session, workspace, "lg-landed-8")
    untouched = _ticket(session, workspace, "lg-landed-9")
    authors.body = "Work.\n\nTicket: lg-landed-7.\nTickets: lg-landed-8, lg-landed-7"

    entry, report = _land(session, workspace, authors)

    for ticket in (named, other, untouched):
        session.refresh(ticket)
    assert (named.state, other.state) == (TicketState.DONE, TicketState.DONE)
    assert named.state_locked and named.resolved_at is not None
    assert untouched.state is not TicketState.DONE
    [event] = _state_events(session, named)
    assert event["actor"] == RESOLVED_BY_QUEUE
    assert (event["pr_number"], event["merge_commit"]) == (PR, entry.merge_commit)
    assert report.errors == []


def test_a_named_ticket_that_cannot_be_closed_is_reported_not_skipped(
    session, workspace, authors, opened
):
    driven = _ticket(session, workspace, "lg-landed-7")
    closed = _ticket(session, workspace, "lg-landed-8")
    closed.state = TicketState.WONT_DO
    session.add(closed)
    session.add(
        OrchestrationRun(
            run_code="orch_landed",
            ticket_id=driven.id,
            workspace_id=workspace.id,
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    session.commit()
    authors.body = "Ticket: lg-landed-7, lg-landed-8, lg-nowhere-404"

    _, report = _land(session, workspace, authors)

    session.refresh(driven)
    session.refresh(closed)
    assert driven.state is not TicketState.DONE
    assert closed.state is TicketState.WONT_DO, "a ticket already closed is left as it is"
    problems = [error for error in report.errors if "landed; ticket" in error]
    assert len(problems) == 2
    assert any("lg-landed-7" in p and "orchestration" in p for p in problems)
    assert any("lg-nowhere-404" in p for p in problems)


def test_a_named_ticket_holding_child_work_is_left_to_its_rollup(
    session, workspace, authors, opened
):
    """PR bodies here name the milestone as often as the work item."""
    milestone = _ticket(session, workspace, "lg-landed-1")
    child = _ticket(session, workspace, "lg-landed-2")
    child.parent_ticket_id = milestone.id
    session.add(child)
    session.commit()
    authors.body = "- Ticket: `lg-landed-1`"

    _, report = _land(session, workspace, authors)

    session.refresh(milestone)
    assert milestone.state is not TicketState.DONE
    assert any("lg-landed-1 to its rollup" in action for action in report.actions)
    assert report.errors == []


def test_named_tickets_reads_only_the_ids_a_ticket_line_opens_with():
    body = (
        "Fixes the gap lg-other-1 left.\n"
        "Ticket: lg-milestone-that-2490. Its first criterion overlaps lg-milestone-that-596.\n"
        "- tickets: `lg-a-1`, lg-b-2 and lg-a-1\n"
        "Ticket: lg-c-3 (under lg-parent-87).\n"
        "Not a ticket: lg-d-4\n"
        "Ticket: see lg-e-5\n"
    )

    assert named_tickets(body) == ["lg-milestone-that-2490", "lg-a-1", "lg-b-2", "lg-c-3"]
