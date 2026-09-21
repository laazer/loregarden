"""`loregarden_pin_stage_agent`: steer one dispatch of a stage to a named agent."""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import Artifact, ArtifactKind
from loregarden.services.studio_routing import (
    resolve_stage_execution,
    ticket_stage_definition,
)
from sqlmodel import Session, select
from tests.test_scope_reroute_stage import _classify_impl_template


def _call(session, name: str, args: dict) -> dict:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def _implement_stage(session, ticket):
    return ticket_stage_definition(session, ticket, "implement")


def test_the_pin_outranks_classify_scoring_and_is_consumed_once(db_session: Session):
    """717's shape: classify defaults to frontend; the pin sends the next
    dispatch to backend, and only the next one."""
    ticket = _classify_impl_template(db_session)
    stage = _implement_stage(db_session, ticket)
    assert resolve_stage_execution(ticket, stage)[0] == "frontend_implementer"

    result = _call(
        db_session,
        "loregarden_pin_stage_agent",
        {
            "ticket_id": ticket.id,
            "agent_id": "backend_implementer",
            "reason": "The frontend implementer reported all criteria need backend work.",
        },
    )

    assert result["pinned"] == {"stage_key": "implement", "agent_id": "backend_implementer"}
    db_session.refresh(ticket)
    assert resolve_stage_execution(ticket, stage)[0] == "backend_implementer"
    note = db_session.exec(
        select(Artifact).where(
            Artifact.ticket_id == ticket.id, Artifact.kind == ArtifactKind.CONTEXT
        )
    ).first()
    assert note is not None and "backend work" in (note.content_json or "")


def test_an_agent_the_stage_cannot_run_is_refused_naming_what_it_offers(db_session: Session):
    ticket = _classify_impl_template(db_session)

    with pytest.raises(ValueError, match="offers .*backend_implementer"):
        _call(
            db_session,
            "loregarden_pin_stage_agent",
            {"ticket_id": ticket.id, "agent_id": "verifier", "reason": "wrong stage"},
        )

    db_session.refresh(ticket)
    assert ticket.scope_reroute_agent == "", "a refused pin leaves nothing behind"


def test_an_unknown_agent_and_an_empty_reason_are_refused(db_session: Session):
    ticket = _classify_impl_template(db_session)
    with pytest.raises(ValueError, match="Unknown agent"):
        _call(
            db_session,
            "loregarden_pin_stage_agent",
            {"ticket_id": ticket.id, "agent_id": "no_such_agent", "reason": "x"},
        )
    with pytest.raises(ValueError, match="reason is required"):
        _call(
            db_session,
            "loregarden_pin_stage_agent",
            {"ticket_id": ticket.id, "agent_id": "backend_implementer", "reason": "  "},
        )
