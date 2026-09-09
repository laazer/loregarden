"""`GET /api/tickets` pages, and its cost stops scaling with the page.

The endpoint returned every ticket in the workspace and rebuilt the same workflow
answer for each one. Measured against a copy of the live database (837 tickets,
1236 agent runs): 13459 queries and 85s for one request, of which 3348 were the
same four-times-per-ticket stage resolution and 837 were a full `agent_runs` scan
apiece looking for the latest run code.

These tests count queries rather than asserting on a duration — a query count is
deterministic, a duration is a property of the machine (the same reason
test_ticket_list_commits.py counts commits).
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
            title=f"Paging fixture {index}",
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


def test_limit_and_offset_return_a_slice_of_the_same_list(client, workspace_slug):
    full = client.get(f"/api/tickets?workspace={workspace_slug}")
    assert full.status_code == 200, full.text
    rows = full.json()
    assert len(rows) >= 4, "seed data no longer has enough tickets to page"

    page = client.get(f"/api/tickets?workspace={workspace_slug}&limit=2&offset=1")
    assert page.status_code == 200, page.text
    # Identical summaries, not merely the same ids: paging must not change what a
    # row says about itself.
    assert page.json() == rows[1:3]


def test_the_unpaged_list_is_still_the_whole_workspace(client, workspace_slug):
    """No default page size. Every caller today asks for a whole workspace, and a
    default would truncate them without saying so."""
    response = client.get(f"/api/tickets?workspace={workspace_slug}")
    assert response.status_code == 200, response.text
    assert len(response.json()) == int(response.headers["X-Total-Count"])


def test_total_count_header_counts_the_filtered_set_not_the_page(client, workspace_slug):
    response = client.get(f"/api/tickets?workspace={workspace_slug}&limit=1")
    assert response.status_code == 200, response.text
    total = int(response.headers["X-Total-Count"])
    assert len(response.json()) == 1
    assert total == len(client.get(f"/api/tickets?workspace={workspace_slug}").json())
    assert total > 1, "a one-row page from a one-row workspace proves nothing"

    filtered = client.get(f"/api/tickets?workspace={workspace_slug}&work_item_type=milestone")
    assert int(filtered.headers["X-Total-Count"]) == len(filtered.json())


def test_an_empty_workspace_filter_still_reports_a_total(client):
    """A missing header would read as "nobody counted", which is not the same
    answer as zero."""
    response = client.get("/api/tickets?workspace=no-such-workspace")
    assert response.status_code == 200, response.text
    assert response.json() == []
    assert response.headers["X-Total-Count"] == "0"


def test_query_count_does_not_scale_with_the_page(client, isolated_db, workspace_slug):
    """The regression this whole change exists to prevent.

    Measured on the live database before the fix: 16 queries per ticket. The
    ceiling here is deliberately loose — it is guarding an order of magnitude,
    not pinning today's exact plan.
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
