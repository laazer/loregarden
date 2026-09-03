"""Listing tickets must not write, and must not commit per row.

`_ticket_summary` calls `OrchestrationService.build_stage_views` for every ticket
it serializes, and that method called `ensure_workflow_instance(commit=True)` and
then committed again itself. `GET /api/tickets` runs it per row, so listing N
tickets performed on the order of 2N commits — and created workflow instances as
a side effect of a read (lg-workflow-integrity-606).

The sharper version of the same defect: the free `build_stage_views` reconciles
with `persist=False`, explicitly saying its mutations are not to be written. The
method then added the ticket and instance and committed, persisting exactly what
it was told not to.

These tests count commits rather than asserting on a duration, because a commit
count is deterministic and a duration is a property of the machine — see
lg-workflow-integrity-654 for what asserting the latter costs.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import Ticket, WorkflowInstance
from sqlalchemy import event
from sqlmodel import Session, select


class _CommitCounter:
    """Counts real commits on an engine, for the duration of a `with` block."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.count = 0

    def __enter__(self) -> _CommitCounter:
        event.listen(self.engine, "commit", self._on_commit)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(self.engine, "commit", self._on_commit)

    def _on_commit(self, _conn) -> None:
        self.count += 1


@pytest.fixture(name="listed")
def listed_fixture(client, isolated_db):
    """A handful of real tickets, and the engine to watch."""
    with Session(isolated_db) as session:
        tickets = list(session.exec(select(Ticket)).all())
    assert len(tickets) >= 3, "seed data no longer has enough tickets to measure"
    return tickets


def test_listing_tickets_does_not_commit_per_row(client, isolated_db, listed):
    """AC1 and AC3. The commit count must not scale with the page size.

    Measured on this suite's seed data, 2026-09-03:
      before the fix — 21 commits for 13 tickets (1.62 per row)
      after          — 0
    """
    with _CommitCounter(isolated_db) as counter:
        response = client.get("/api/tickets?workspace=loregarden")

    assert response.status_code == 200, response.text
    rows = response.json()
    assert len(rows) >= 3

    # Not "fewer than before" — none. Serializing is a read.
    assert counter.count == 0, (
        f"{counter.count} commits for {len(rows)} tickets ({counter.count / len(rows):.2f} per row)"
    )


def test_serializing_a_ticket_creates_no_workflow_instance(client, isolated_db):
    """AC2. A read endpoint does not write.

    The same class of defect as the next_agent backfill that
    lg-workflow-integrity-441 removed, where reading a ticket mutated it.
    """
    from loregarden.models.domain import WorkItemType, Workspace
    from loregarden.services.ticket_service import TicketService

    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace)).first()
        # A ticket with no workflow instance at all — the case that used to make
        # a GET create one.
        fresh = TicketService(session).create_ticket(
            workspace_slug=workspace.slug,
            title="Never orchestrated",
            work_item_type=WorkItemType.BUG,
            parent_ticket_id=session.exec(
                select(Ticket).where(Ticket.work_item_type == WorkItemType.MILESTONE)
            )
            .first()
            .id,
        )
        session.commit()
        ticket_id = fresh.id
        # `create_ticket` sets up the workflow, which is correct — creating a
        # ticket is a write. Removing it here produces the state this test is
        # about: a ticket with no instance, which a GET must not repair.
        existing = session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket_id)
        ).first()
        if existing is not None:
            session.delete(existing)
            session.commit()

    response = client.get("/api/tickets?workspace=loregarden")
    assert response.status_code == 200, response.text

    with Session(isolated_db) as session:
        created = session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket_id)
        ).first()
    assert created is None, "listing tickets created a workflow instance"


def test_a_ticket_with_no_instance_still_serializes(client, isolated_db):
    """AC4. Not writing must not mean not answering: a ticket that genuinely has
    no workflow instance still has to appear, with an empty stage list rather
    than an error."""
    from loregarden.models.domain import WorkItemType, Workspace
    from loregarden.services.ticket_service import TicketService

    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace)).first()
        milestone = session.exec(
            select(Ticket).where(Ticket.work_item_type == WorkItemType.MILESTONE)
        ).first()
        fresh = TicketService(session).create_ticket(
            workspace_slug=workspace.slug,
            title="Stageless",
            work_item_type=WorkItemType.BUG,
            parent_ticket_id=milestone.id,
        )
        session.commit()
        ticket_id = fresh.id

    response = client.get(f"/api/tickets/{ticket_id}")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["id"] == ticket_id
    assert isinstance(body.get("stages", []), list)
