from datetime import datetime

from fastapi.testclient import TestClient
from loregarden.models.domain import TicketState, WorkflowInstance, WorkflowTemplate
from sqlmodel import Session
from tests.factories import make_ticket, make_workspace


def _template(session: Session, slug: str) -> WorkflowTemplate:
    template = WorkflowTemplate(slug=slug, name=slug)
    session.add(template)
    session.commit()
    session.refresh(template)
    return template


def _use(
    session: Session, template: WorkflowTemplate, state: TicketState, at: datetime | None = None
) -> None:
    workspace = make_workspace(session, slug="usage")
    ticket = make_ticket(session, workspace_id=workspace.id, state=state)
    instance = WorkflowInstance(ticket_id=ticket.id, template_id=template.id)
    if at:
        instance.created_at = at
    session.add(instance)
    session.commit()


def _by_slug(client: TestClient) -> dict[str, dict]:
    response = client.get("/api/studio/workflow-usage")
    assert response.status_code == 200
    return {row["template_slug"]: row for row in response.json()}


def test_counts_tickets_and_open_tickets_per_template(
    client: TestClient, db_session: Session
) -> None:
    busy = _template(db_session, "busy-flow")
    _use(db_session, busy, TicketState.BACKLOG)
    _use(db_session, busy, TicketState.IN_PROGRESS)
    _use(db_session, busy, TicketState.DONE)
    _use(db_session, busy, TicketState.WONT_DO)

    row = _by_slug(client)["busy-flow"]

    # Done and won't-do are closed; backlog and in progress are open.
    assert (row["tickets"], row["open"]) == (4, 2)


def test_lists_a_template_no_ticket_has_used(client: TestClient, db_session: Session) -> None:
    _template(db_session, "unused-flow")

    row = _by_slug(client)["unused-flow"]

    assert (row["tickets"], row["open"], row["last_used_at"]) == (0, 0, None)


def test_last_used_is_the_latest_ticket_to_start_on_it(
    client: TestClient, db_session: Session
) -> None:
    flow = _template(db_session, "dated-flow")
    _use(db_session, flow, TicketState.DONE, at=datetime(2026, 10, 1, 9, 0, 0))
    _use(db_session, flow, TicketState.DONE, at=datetime(2026, 10, 24, 9, 0, 0))

    assert _by_slug(client)["dated-flow"]["last_used_at"].startswith("2026-10-24T09:00:00")
