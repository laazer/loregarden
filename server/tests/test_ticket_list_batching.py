"""A page of ticket summaries must not query per row.

`GET /api/tickets` resolves each ticket's workflow four times — template, stage
cursor, stage views, stage agent — and every one of those used to re-query the
ticket's `WorkflowInstance` and re-fetch its `Workspace`. It also asked
`agent_runs` for a run code once per ticket, against no index behind
`ORDER BY created_at DESC`. Measured on a copy of the live database (837
tickets, 1236 runs): 3347 instance queries, 837 workspace reads, and 837 table
scans for run codes — 13459 queries and 85s for one request.

Like `test_ticket_list_commits`, these count work rather than asserting on a
duration: a query count is deterministic, a duration is a property of the
machine.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import AgentRun, RunStatus, Ticket, WorkItemType, Workspace
from loregarden.services.ticket_service import TicketService
from sqlalchemy import event
from sqlmodel import Session, select


class _QueryCounter:
    """Counts statements executed on an engine, for the duration of a `with` block."""

    def __init__(self, engine) -> None:
        self.engine = engine
        self.count = 0

    def __enter__(self) -> _QueryCounter:
        event.listen(self.engine, "before_cursor_execute", self._on_execute)
        return self

    def __exit__(self, *_exc) -> None:
        event.remove(self.engine, "before_cursor_execute", self._on_execute)

    def _on_execute(self, *_args, **_kwargs) -> None:
        self.count += 1


def _add_tickets(session: Session, workspace: Workspace, count: int) -> list[str]:
    parent = session.exec(
        select(Ticket).where(Ticket.work_item_type == WorkItemType.MILESTONE)
    ).first()
    service = TicketService(session)
    ids = []
    for index in range(count):
        ticket = service.create_ticket(
            workspace_slug=workspace.slug,
            title=f"Batching fixture {index}",
            work_item_type=WorkItemType.BUG,
            parent_ticket_id=parent.id,
        )
        ids.append(ticket.id)
    session.commit()
    return ids


@pytest.fixture(name="workspace_slug")
def workspace_slug_fixture(client, isolated_db) -> str:
    with Session(isolated_db) as session:
        return session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one().slug


def test_query_count_does_not_scale_with_the_page(client, isolated_db, workspace_slug):
    """The regression this change exists to prevent.

    Measured on the live database before the fix: 16 queries per ticket. The
    ceiling is deliberately loose — it guards an order of magnitude, not today's
    exact plan.
    """
    with _QueryCounter(isolated_db) as base:
        first = client.get(f"/api/tickets?workspace={workspace_slug}")
    assert first.status_code == 200, first.text
    base_rows = len(first.json())

    added = 12
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).one()
        _add_tickets(session, workspace, added)

    with _QueryCounter(isolated_db) as grown:
        second = client.get(f"/api/tickets?workspace={workspace_slug}")
    assert len(second.json()) == base_rows + added

    per_row = (grown.count - base.count) / added
    assert per_row < 4, (
        f"{grown.count - base.count} extra queries for {added} extra tickets "
        f"({per_row:.1f} per row); the per-ticket workflow resolution is back"
    )


def test_run_code_is_the_latest_run_for_each_ticket(client, isolated_db, workspace_slug):
    """The batched lookup answers what the per-ticket query answered: the newest
    run's code, and "" for a ticket that has never run."""
    now = datetime.now(timezone.utc)
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).one()
        ticket_id = _add_tickets(session, workspace, 1)[0]
        for offset, code in ((2, "run-older"), (0, "run-newest"), (1, "run-middle")):
            session.add(
                AgentRun(
                    run_code=code,
                    ticket_id=ticket_id,
                    workspace_id=workspace.id,
                    agent_id="backend_implementer",
                    status=RunStatus.SUCCEEDED,
                    created_at=now - timedelta(minutes=offset),
                )
            )
        never_ran = _add_tickets(session, workspace, 1)[0]
        session.commit()

    rows = {row["id"]: row for row in client.get(f"/api/tickets?workspace={workspace_slug}").json()}
    assert rows[ticket_id]["run_code"] == "run-newest"
    assert rows[never_ran]["run_code"] == ""


def test_a_paged_listing_only_resolves_its_page(client, isolated_db, workspace_slug):
    """The batches follow `limit`: a page of 2 must not price the whole workspace."""
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == workspace_slug)).one()
        _add_tickets(session, workspace, 12)

    with _QueryCounter(isolated_db) as whole:
        client.get(f"/api/tickets?workspace={workspace_slug}")
    with _QueryCounter(isolated_db) as page:
        response = client.get(f"/api/tickets?workspace={workspace_slug}&limit=2")

    assert len(response.json()) == 2
    assert page.count < whole.count
