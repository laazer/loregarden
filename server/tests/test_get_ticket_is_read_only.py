"""`loregarden_get_ticket` and `_by_external` are reads, and write nothing.

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
from loregarden.mcp.ticket_edit_tools import ticket_state_payload
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import WorkflowInstance
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


def _read(session: Session, tool: str, ticket) -> dict:
    arguments = (
        {"ticket_id": ticket.id}
        if tool == "loregarden_get_ticket"
        else {"workspace_slug": "loregarden", "external_id": ticket.external_id}
    )
    return json.loads(execute_tool(session, tool, arguments))


READ_TOOLS = ["loregarden_get_ticket", "loregarden_get_ticket_by_external"]


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_reading_a_ticket_with_a_workflow_instance_writes_nothing(db_session, isolated_db, tool):
    ticket = make_workspace_ticket(db_session, "lg-read-only-1")
    written_stages = ticket_state_payload(db_session, ticket.id)["stages"]
    assert db_session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
    ).first(), "the write path should have created the instance this case is about"

    with _writes(isolated_db) as writes:
        payload = _read(db_session, tool, ticket)

    assert writes == []
    assert payload["stages"] == written_stages


@pytest.mark.parametrize("tool", READ_TOOLS)
def test_reading_a_ticket_with_no_workflow_instance_creates_none(db_session, isolated_db, tool):
    ticket = make_workspace_ticket(db_session, "lg-read-only-2")

    with _writes(isolated_db) as writes:
        payload = _read(db_session, tool, ticket)

    assert writes == []
    assert payload["stages"], "a ticket with no instance still shows the stages it would have"
    assert (
        db_session.exec(
            select(WorkflowInstance).where(WorkflowInstance.ticket_id == ticket.id)
        ).first()
        is None
    )
