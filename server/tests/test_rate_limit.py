"""A ceiling on how fast a registered server may be called."""

from datetime import datetime, timedelta, timezone

from loregarden.models.domain import McpServer, McpToolCall
from loregarden.services.rate_limit import calls_in_window, rate_limit_denial
from loregarden.services.tool_telemetry import (
    DECISION_APPROVED,
    DECISION_RATE_LIMITED,
    DECISION_REJECTED,
    DECISION_TRUSTED_SERVER,
)
from sqlmodel import Session


def _server(session: Session, *, limit: int = 0, name: str = "github") -> McpServer:
    server = McpServer(
        name=name, transport="http", url="https://mcp.example/sse", rate_limit_per_min=limit
    )
    session.add(server)
    session.commit()
    return server


def _calls(
    session: Session,
    n: int,
    *,
    server: str = "github",
    decision: str = DECISION_TRUSTED_SERVER,
    age_s: int = 0,
) -> None:
    when = datetime.now(timezone.utc) - timedelta(seconds=age_s)
    for i in range(n):
        session.add(
            McpToolCall(
                run_id="r",
                ticket_id="t",
                agent_id="planner",
                tool_name=f"mcp__{server}__do_{i}",
                server_name=server,
                decision=decision,
                created_at=when,
            )
        )
    session.commit()


def test_no_limit_set_means_no_ceiling(db_session: Session):
    """The default. A limit nobody configured must not start refusing work."""
    _server(db_session, limit=0)
    _calls(db_session, 500)

    assert rate_limit_denial(db_session, "github") == ""


def test_under_the_limit_proceeds(db_session: Session):
    _server(db_session, limit=10)
    _calls(db_session, 9)

    assert rate_limit_denial(db_session, "github") == ""


def test_at_the_limit_refuses(db_session: Session):
    _server(db_session, limit=10)
    _calls(db_session, 10)

    denial = rate_limit_denial(db_session, "github")
    assert "Rate limit reached" in denial
    # The agent reads this as its tool failure, so it has to say what to do.
    assert "Wait before calling it again" in denial
    assert "limit 10" in denial


def test_refused_calls_do_not_consume_the_budget(db_session: Session):
    """Otherwise a burst of refusals would keep the server locked out.

    A rejected or rate-limited call never reached the server, so counting it
    would let the limit feed on itself.
    """
    _server(db_session, limit=5)
    _calls(db_session, 20, decision=DECISION_REJECTED)
    _calls(db_session, 20, decision=DECISION_RATE_LIMITED)

    assert calls_in_window(db_session, "github") == 0
    assert rate_limit_denial(db_session, "github") == ""


def test_calls_a_human_approved_do_consume_it(db_session: Session):
    """They reached the server, however they were authorised."""
    _server(db_session, limit=3)
    _calls(db_session, 3, decision=DECISION_APPROVED)

    assert rate_limit_denial(db_session, "github") != ""


def test_older_calls_fall_out_of_the_window(db_session: Session):
    _server(db_session, limit=5)
    _calls(db_session, 20, age_s=90)

    assert calls_in_window(db_session, "github") == 0
    assert rate_limit_denial(db_session, "github") == ""


def test_the_limit_is_per_server(db_session: Session):
    """One busy server must not throttle a quiet one."""
    _server(db_session, limit=5, name="github")
    _server(db_session, limit=5, name="linear")
    _calls(db_session, 10, server="github")

    assert rate_limit_denial(db_session, "github") != ""
    assert rate_limit_denial(db_session, "linear") == ""


def test_an_unregistered_server_has_no_limit(db_session: Session):
    assert rate_limit_denial(db_session, "never-registered") == ""


def test_a_broken_limit_does_not_block_work(db_session: Session):
    """The failure mode has to be "no limit", never "no tools"."""
    from unittest.mock import patch

    _server(db_session, limit=1)
    _calls(db_session, 10)

    with patch("loregarden.services.rate_limit.calls_in_window", side_effect=RuntimeError("boom")):
        assert rate_limit_denial(db_session, "github") == ""
