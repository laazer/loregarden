"""Every Loregarden MCP call an agent makes is one ledger row, whoever ran it.

The bridge stopped seeing calls on 2026-08-14 — bypass, print-mode cursor,
codex and external harnesses have no bridge — so the count moved to dispatch,
the one seam every transport crosses (lg-workflow-integrity-759).
"""

from fastapi.testclient import TestClient
from loregarden.models.domain import (
    AgentRun,
    McpToolCall,
    RunStatus,
    Ticket,
    WorkItemType,
)
from loregarden.services.ticket_service import TicketService
from loregarden.services.tool_telemetry import (
    DECISION_ALLOWLIST,
    DECISION_APPROVED,
    DECISION_EXECUTED,
    DECISION_FAILED,
    DECISION_REJECTED,
    bridge_would_double_count,
)
from sqlmodel import Session, select


def _rpc(client: TestClient, tool: str, args: dict) -> dict:
    res = client.post(
        "/mcp",
        json={
            "jsonrpc": "2.0",
            "id": 7,
            "method": "tools/call",
            "params": {"name": tool, "arguments": args},
        },
        headers={"X-Loregarden-Orchestrated": "1"},
    )
    assert res.status_code == 200
    return res.json()


def _ticket(session: Session) -> Ticket:
    return TicketService(session).create_ticket(
        workspace_slug="loregarden", title="ledger", work_item_type=WorkItemType.MILESTONE
    )


def _running(session: Session, ticket: Ticket) -> AgentRun:
    run = AgentRun(
        run_code="run-ledger",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _rows(session: Session) -> list[McpToolCall]:
    return list(session.exec(select(McpToolCall)).all())


def test_a_call_over_the_http_transport_is_one_row_attributed_to_the_running_agent(
    client: TestClient, db_session: Session
):
    ticket = _ticket(db_session)
    run = _running(db_session, ticket)

    body = _rpc(client, "loregarden_get_ticket", {"ticket_id": ticket.id})

    assert not body["result"].get("isError")
    [row] = _rows(db_session)
    assert row.tool_name == "mcp__loregarden__loregarden_get_ticket"
    assert row.server_name == "loregarden"
    assert row.decision == DECISION_EXECUTED
    assert (row.run_id, row.ticket_id, row.agent_id) == (
        run.id,
        ticket.id,
        "backend_implementer",
    )


def test_a_call_that_names_a_ticket_by_external_id_is_attributed(
    client: TestClient, db_session: Session
):
    ticket = _ticket(db_session)

    _rpc(client, "loregarden_get_ticket", {"external_id": ticket.external_id})

    [row] = _rows(db_session)
    assert row.ticket_id == ticket.id
    # No agent run in flight: the call is still counted, just unattributed.
    assert row.run_id == ""


def test_a_failing_call_is_recorded_as_failed_not_dropped(client: TestClient, db_session: Session):
    body = _rpc(client, "loregarden_get_ticket", {"ticket_id": "does-not-exist"})

    assert body["result"]["isError"]
    [row] = _rows(db_session)
    assert row.decision == DECISION_FAILED
    assert row.ticket_id == ""


def test_a_call_the_stdio_server_handles_is_recorded_the_same_way(db_session: Session):
    """The stdio in-process server hands `tools/call` to the same handler."""
    from loregarden.mcp.protocol import handle_message

    ticket = _ticket(db_session)
    handle_message(
        db_session,
        {
            "jsonrpc": "2.0",
            "id": 1,
            "method": "tools/call",
            "params": {"name": "loregarden_get_ticket", "arguments": {"ticket_id": ticket.id}},
        },
        orchestrated=True,
    )

    [row] = _rows(db_session)
    assert row.decision == DECISION_EXECUTED
    assert row.ticket_id == ticket.id


def test_the_bridge_leaves_loregarden_calls_it_waved_through_to_dispatch():
    """Dispatch will count the call the bridge auto-approved; a second row
    from the bridge would double it. A human's decision, a refusal, and every
    other server stay the bridge's."""
    assert bridge_would_double_count("mcp__loregarden__loregarden_get_ticket", DECISION_ALLOWLIST)
    assert not bridge_would_double_count(
        "mcp__loregarden__loregarden_get_ticket", DECISION_APPROVED
    )
    assert not bridge_would_double_count(
        "mcp__loregarden__loregarden_get_ticket", DECISION_REJECTED
    )
    assert not bridge_would_double_count("mcp__github__create_issue", DECISION_ALLOWLIST)
    assert not bridge_would_double_count("Bash", DECISION_ALLOWLIST)
