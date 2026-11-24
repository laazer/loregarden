"""Every read of a ticket's state writes nothing: the two MCP read tools and
the two REST state routes, which build the same payload on the same read path.

Both returned `ticket_state_payload`, whose stage map came from
`build_stage_views`, which commits the ticket and its workflow instance. So every
read moved `tickets.updated_at` with no event behind it. On
lg-milestone-that-2196 that read as a state change 52 minutes after the ticket's
last real one, in the middle of working out why it had seemed slow to finish.
"""

from __future__ import annotations

import json
from contextlib import contextmanager

import pytest
from fastapi.testclient import TestClient
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import WorkflowInstance
from loregarden.services.ticket_state_payload import ticket_state_payload
from sqlalchemy import event
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket

WRITES = ("INSERT", "UPDATE", "DELETE")


@contextmanager
def _writes(engine):
    seen: list[str] = []

    def record(_conn, _cursor, statement, _params, _context, _many):
        if statement.lstrip().upper().startswith(WRITES):
            seen.append(statement)

    event.listen(engine, "before_cursor_execute", record)
    try:
        yield seen
    finally:
        event.remove(engine, "before_cursor_execute", record)


def _read(client: TestClient, session: Session, reader: str, ticket) -> dict:
    if reader == "loregarden_get_ticket":
        return json.loads(execute_tool(session, reader, {"ticket_id": ticket.id}))
    if reader == "loregarden_get_ticket_by_external":
        arguments = {"workspace_slug": "loregarden", "external_id": ticket.external_id}
        return json.loads(execute_tool(session, reader, arguments))
    path = (
        f"/api/orchestration/tickets/{ticket.id}/state"
        if reader == "GET /tickets/{id}/state"
        else f"/api/orchestration/tickets/by-external/loregarden/{ticket.external_id}/state"
    )
    res = client.get(path)
    assert res.status_code == 200, res.text
    return res.json()


READERS = [
    "loregarden_get_ticket",
    "loregarden_get_ticket_by_external",
    "GET /tickets/{id}/state",
    "GET /tickets/by-external/{slug}/{external_id}/state",
]


@pytest.mark.parametrize("reader", READERS)
def test_reading_a_ticket_with_a_workflow_instance_writes_nothing(
    client, db_session, isolated_db, reader
):
    ticket = make_workspace_ticket(db_session, "lg-read-only-1")
    written_stages = ticket_state_payload(db_session, ticket.id)["stages"]
    assert db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first(), "the write path should have created the instance this case is about"

    with _writes(isolated_db) as writes:
        payload = _read(client, db_session, reader, ticket)

    assert writes == []
    assert payload["stages"] == written_stages


@pytest.mark.parametrize("reader", READERS)
def test_reading_a_ticket_with_no_workflow_instance_creates_none(
    client, db_session, isolated_db, reader
):
    ticket = make_workspace_ticket(db_session, "lg-read-only-2")

    with _writes(isolated_db) as writes:
        payload = _read(client, db_session, reader, ticket)

    assert writes == []
    assert payload["stages"], "a ticket with no instance still shows the stages it would have"
    assert (
        db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        is None
    )


def test_rest_and_mcp_return_one_payload(client, db_session):
    """The REST route was a hand-kept copy that had lost `revision` and
    `current_stage_shape`; now both transports return the same state."""
    ticket = make_workspace_ticket(db_session, "lg-read-only-3")
    over_mcp = _read(client, db_session, "loregarden_get_ticket", ticket)
    over_rest = _read(client, db_session, "GET /tickets/{id}/state", ticket)

    assert over_rest == {key: over_mcp[key] for key in over_rest}
    assert set(over_rest) == set(over_mcp) - {
        "title",
        "description",
        "acceptance_criteria",
        "out_of_scope",
        "documents",
        "kept_code",
    }
