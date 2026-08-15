"""An agent can read the requirement it was handed.

`loregarden_get_ticket` returned where a ticket sat in the pipeline and not what
it asked for: no `title`, no `description`, no `acceptance_criteria` anywhere in
the MCP surface. `CLAUDE.md` already names the consequence — "agents invent ACs
into test docstrings that then steer every later stage" — and told agents to
read the real database row instead, which an orchestrated agent cannot do: MCP
is the interface it has, and in a worktree the sqlite path resolves to an empty
database.

These pin the fix and the shape of it. The body rides on the two *read* tools
and deliberately not on `ticket_state_payload`, which every write tool returns
as well.
"""

from __future__ import annotations

import json

from loregarden.mcp.ticket_edit_tools import ticket_state_payload
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import UpdateTicketRequest, WorkItemType
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_service import TicketService

_BODY_KEYS = {"title", "description", "acceptance_criteria"}


def _call(session, name: str, args: dict) -> dict:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def _ticket_with_body(session, *, title: str, description: str, criteria: list[str]):
    """A milestone carrying a real requirement — parentless, so no chain to build."""
    ticket = TicketService(session).create_ticket(
        workspace_slug="loregarden",
        title=title,
        work_item_type=WorkItemType.MILESTONE,
    )
    OrchestrationService(session).update_ticket_manual(
        ticket,
        UpdateTicketRequest(description=description, acceptance_criteria=criteria),
    )
    session.refresh(ticket)
    return ticket


# ---- the requirement is readable ---------------------------------------


def test_get_ticket_returns_the_requirement(db_session):
    """The whole point: id in, requirement out, without a database read."""
    ticket = _ticket_with_body(
        db_session,
        title="Lanes must release on every terminal exit",
        description="A lane held by a finished orchestration is a lane nobody can use.",
        criteria=["A blocked ticket releases its lane", "A cancelled one does too"],
    )

    payload = _call(db_session, "loregarden_get_ticket", {"ticket_id": ticket.id})

    assert payload["title"] == "Lanes must release on every terminal exit"
    assert "nobody can use" in payload["description"]
    assert payload["acceptance_criteria"] == [
        "A blocked ticket releases its lane",
        "A cancelled one does too",
    ]


def test_get_ticket_by_external_returns_the_same_body(db_session):
    """Two ways in, one answer — an agent holding either identifier is served."""
    ticket = _ticket_with_body(
        db_session,
        title="Resolvable by slug",
        description="Body reachable through the external_id path too.",
        criteria=["Same payload either way"],
    )

    by_id = _call(db_session, "loregarden_get_ticket", {"ticket_id": ticket.id})
    by_external = _call(
        db_session,
        "loregarden_get_ticket_by_external",
        {"external_id": ticket.external_id, "workspace_slug": "loregarden"},
    )

    assert {key: by_external[key] for key in _BODY_KEYS} == {key: by_id[key] for key in _BODY_KEYS}


def test_an_empty_requirement_reads_as_empty_rather_than_missing(db_session):
    """An unwritten requirement must not look like an unreadable one.

    The keys are present and falsy. An agent that can tell the difference asks
    for a requirement; one that cannot invents it, which is the failure the
    body was added to stop.
    """
    ticket = TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="Nothing written down yet",
        work_item_type=WorkItemType.MILESTONE,
    )

    payload = _call(db_session, "loregarden_get_ticket", {"ticket_id": ticket.id})

    assert _BODY_KEYS <= payload.keys()
    assert payload["description"] == ""
    assert payload["acceptance_criteria"] == []


def test_the_routing_payload_still_rides_along(db_session):
    """Widening a read must not cost the caller what it already relied on."""
    ticket = _ticket_with_body(db_session, title="Still routable", description="d", criteria=["c"])

    payload = _call(db_session, "loregarden_get_ticket", {"ticket_id": ticket.id})

    assert {
        "ticket_id",
        "external_id",
        "state",
        "workflow_stage_key",
        "workflow_stage_status",
        "stages",
        "hierarchy",
    } <= payload.keys()


# ---- the shape: reads carry the body, writes do not --------------------


def test_a_write_reply_stays_a_routing_answer(db_session):
    """`complete_stage` asks "where am I now", not "what am I meant to do".

    Every write tool returns `ticket_state_payload`. Putting the body there
    would replay a long description into context on each of them, so the two
    read tools are widened and this one is left alone. If the body ever does
    belong on writes, that is a decision worth making deliberately — this fails
    when it is made by accident.
    """
    ticket = _ticket_with_body(
        db_session,
        title="Long requirement",
        description="x" * 4000,
        criteria=["a", "b", "c"],
    )

    state_only = ticket_state_payload(db_session, ticket.id)

    assert not (_BODY_KEYS & state_only.keys())
