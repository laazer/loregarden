"""What agents asked to do, and how each request was resolved."""

from unittest.mock import patch

from loregarden.models.domain import McpToolCall
from loregarden.services.tool_telemetry import (
    DECISION_ALLOWLIST,
    DECISION_APPROVED,
    DECISION_REJECTED,
    DECISION_TRUSTED_SERVER,
    counts_by_decision,
    counts_by_server,
    recent_calls,
    record_tool_call,
)
from sqlmodel import Session, select


def _call(session: Session, tool: str, decision: str = DECISION_ALLOWLIST, **kwargs) -> None:
    record_tool_call(
        session,
        run_id=kwargs.pop("run_id", "run-1"),
        ticket_id=kwargs.pop("ticket_id", "t-1"),
        agent_id=kwargs.pop("agent_id", "planner"),
        tool_name=tool,
        decision=decision,
        **kwargs,
    )


def test_the_server_is_parsed_from_the_tool_name(db_session: Session):
    _call(db_session, "mcp__github__create_issue", DECISION_TRUSTED_SERVER)
    row = db_session.exec(select(McpToolCall)).one()
    assert row.server_name == "github"
    assert row.tool_name == "mcp__github__create_issue"


def test_a_non_mcp_tool_has_no_server(db_session: Session):
    """Bash and friends still get a row — they are decisions too — but they do
    not belong to any server, and must not invent one."""
    _call(db_session, "Bash")
    assert db_session.exec(select(McpToolCall)).one().server_name == ""


def test_counts_group_by_server_and_ignore_non_mcp(db_session: Session):
    _call(db_session, "mcp__github__a")
    _call(db_session, "mcp__github__b")
    _call(db_session, "mcp__loregarden__loregarden_get_ticket")
    _call(db_session, "Bash")

    # This is the gateway's view; a shell command is not a server.
    assert counts_by_server(db_session) == {"github": 2, "loregarden": 1}


def test_counts_group_by_how_it_was_resolved(db_session: Session):
    _call(db_session, "mcp__github__a", DECISION_TRUSTED_SERVER)
    _call(db_session, "mcp__github__b", DECISION_APPROVED)
    _call(db_session, "mcp__github__c", DECISION_REJECTED)

    assert counts_by_decision(db_session) == {
        DECISION_TRUSTED_SERVER: 1,
        DECISION_APPROVED: 1,
        DECISION_REJECTED: 1,
    }


def test_the_feed_is_newest_first(db_session: Session):
    for index in range(3):
        _call(db_session, f"mcp__github__tool_{index}")
    assert [c.tool_name for c in recent_calls(db_session, limit=2)] == [
        "mcp__github__tool_2",
        "mcp__github__tool_1",
    ]


def test_a_human_decision_records_how_long_it_took(db_session: Session):
    _call(db_session, "mcp__github__a", DECISION_APPROVED, decision_ms=4200)
    assert db_session.exec(select(McpToolCall)).one().decision_ms == 4200


def test_a_negative_duration_is_clamped(db_session: Session):
    """A clock that moved backwards should not produce a negative wait."""
    _call(db_session, "mcp__github__a", DECISION_APPROVED, decision_ms=-5)
    assert db_session.exec(select(McpToolCall)).one().decision_ms == 0


def test_recording_never_fails_the_run(db_session: Session):
    """The agent's work is the point. A telemetry write that raises would end a
    run over bookkeeping."""
    with patch.object(db_session, "commit", side_effect=RuntimeError("disk full")):
        _call(db_session, "mcp__github__a")  # must not raise


def test_the_endpoint_reports_calls_and_counts(client, db_session: Session):
    _call(db_session, "mcp__github__create_issue", DECISION_TRUSTED_SERVER)
    _call(db_session, "mcp__loregarden__loregarden_get_ticket", DECISION_ALLOWLIST)

    body = client.get("/api/mcp-servers/telemetry").json()
    assert body["by_server"] == {"github": 1, "loregarden": 1}
    assert body["by_decision"] == {DECISION_TRUSTED_SERVER: 1, DECISION_ALLOWLIST: 1}
    assert [c["tool_name"] for c in body["recent"]][0] == "mcp__loregarden__loregarden_get_ticket"
