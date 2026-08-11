"""The operator moves the triage rail can now make itself.

Baxter could always describe these; it could not do them, so every one of them
ended as a list of clicks for the operator. Each test pins the behaviour that
made the operation worth a tool rather than a note in the reply.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments, tool_names
from loregarden.models.domain import StageStatus, Ticket, TicketState, WorkItemType, Workspace
from loregarden.services.acceptance_criteria import load_criteria
from loregarden.services.stage_retry_budget import (
    count_stage_dispatches,
    record_stage_dispatch,
)
from loregarden.services.ticket_relations import TicketRelationService
from loregarden.services.ticket_service import TicketService
from sqlmodel import select


def _call(session, name: str, args: dict) -> dict:
    return json.loads(execute_tool(session, name, normalize_tool_arguments(name, args)))


def _workspace(session, slug: str) -> Workspace:
    return session.exec(select(Workspace).where(Workspace.slug == slug)).first()


def _task(session) -> Ticket:
    ws = _workspace(session, "loregarden")
    return session.exec(
        select(Ticket).where(
            Ticket.workspace_id == ws.id,
            Ticket.work_item_type == WorkItemType.TASK,
        )
    ).first()


def _new_subtree(session, title: str) -> tuple[Ticket, Ticket]:
    """A (capability, task) pair under a fresh milestone → feature chain.

    The hierarchy rules are strict — only a milestone may be parentless, and a
    feature holds capabilities rather than tasks — so a test that needs a real
    subtree builds the whole chain rather than two loose rows.
    """
    svc = TicketService(session)
    milestone = svc.create_ticket(
        workspace_slug="loregarden",
        title=f"{title} — milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    feature = svc.create_ticket(
        workspace_slug="loregarden",
        title=f"{title} — feature",
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=milestone.id,
    )
    capability = svc.create_ticket(
        workspace_slug="loregarden",
        title=title,
        work_item_type=WorkItemType.CAPABILITY,
        parent_ticket_id=feature.id,
    )
    task = svc.create_ticket(
        workspace_slug="loregarden",
        title=f"{title} — task",
        work_item_type=WorkItemType.TASK,
        parent_ticket_id=capability.id,
    )
    return capability, task


@pytest.fixture
def other_workspace(db_session) -> Workspace:
    existing = _workspace(db_session, "elsewhere")
    if existing:
        return existing
    workspace = Workspace(slug="elsewhere", name="Elsewhere", repo_path="/tmp/elsewhere")
    db_session.add(workspace)
    db_session.commit()
    db_session.refresh(workspace)
    return workspace


# --- registration ------------------------------------------------------------


@pytest.mark.parametrize(
    "name",
    [
        "loregarden_move_ticket_workspace",
        "loregarden_set_ticket_workflow",
        "loregarden_requeue_ticket",
        "loregarden_supersede_ticket",
    ],
)
def test_tools_are_registered(name: str):
    assert name in tool_names()


def test_ops_tools_are_denied_to_orchestrated_agents(db_session):
    """A pipeline stage must not rehome or unblock the ticket it is running.

    A stage that could clear its own retry budget would defeat the circuit
    breaker that stopped it.
    """
    ticket = _task(db_session)
    with pytest.raises(ValueError, match="not available"):
        execute_tool(
            db_session,
            "loregarden_requeue_ticket",
            {"ticket_id": ticket.id, "reason": "because"},
            orchestrated=True,
        )


# --- move between workspaces -------------------------------------------------


def test_move_takes_the_subtree_with_it(db_session, other_workspace):
    parent, child = _new_subtree(db_session, "Parent that moves")

    result = _call(
        db_session,
        "loregarden_move_ticket_workspace",
        {"ticket_id": parent.id, "workspace_slug": "elsewhere", "detach_parent": True},
    )

    assert result["workspace_slug"] == "elsewhere"
    assert {item["id"] for item in result["moved"]} == {parent.id, child.id}
    db_session.refresh(parent)
    db_session.refresh(child)
    assert parent.workspace_id == other_workspace.id
    assert child.workspace_id == other_workspace.id


def test_move_refuses_to_strand_a_cross_workspace_parent(db_session, other_workspace):
    """The corruption this tool exists to fix is not one it may create."""
    parent, child = _new_subtree(db_session, "Parent that stays")

    with pytest.raises(ValueError, match="has a parent in the workspace it is leaving"):
        _call(
            db_session,
            "loregarden_move_ticket_workspace",
            {"ticket_id": child.id, "workspace_slug": "elsewhere"},
        )

    db_session.refresh(child)
    assert child.workspace_id == parent.workspace_id


def test_move_detaches_when_told_to(db_session, other_workspace):
    parent, child = _new_subtree(db_session, "Parent left behind")

    _call(
        db_session,
        "loregarden_move_ticket_workspace",
        {"ticket_id": child.id, "workspace_slug": "elsewhere", "detach_parent": True},
    )

    db_session.refresh(child)
    assert child.workspace_id == other_workspace.id
    assert not child.parent_ticket_id


def test_move_rejects_an_unknown_workspace(db_session):
    with pytest.raises(ValueError, match="Unknown workspace"):
        _call(
            db_session,
            "loregarden_move_ticket_workspace",
            {"ticket_id": _task(db_session).id, "workspace_slug": "no-such-repo"},
        )


# --- workflow and stage assignment -------------------------------------------


def test_set_workflow_moves_the_current_stage(db_session):
    ticket = _task(db_session)
    stages = json.loads(
        execute_tool(
            db_session,
            "loregarden_get_ticket",
            {"ticket_id": ticket.id},
        )
    )["stages"]
    target = stages[1]["key"]

    result = _call(
        db_session,
        "loregarden_set_ticket_workflow",
        {"ticket_id": ticket.id, "stage_key": target},
    )

    assert result["workflow_stage_key"] == target


def test_set_workflow_rejects_a_status_without_a_stage(db_session):
    with pytest.raises(ValueError, match="stage_status was given without stage_key"):
        _call(
            db_session,
            "loregarden_set_ticket_workflow",
            {"ticket_id": _task(db_session).id, "stage_status": "pending"},
        )


def test_set_workflow_refuses_a_call_that_changes_nothing(db_session):
    with pytest.raises(ValueError, match="Nothing to change"):
        _call(
            db_session,
            "loregarden_set_ticket_workflow",
            {"ticket_id": _task(db_session).id},
        )


# --- requeue -----------------------------------------------------------------


def test_requeue_clears_the_block_and_restores_the_dispatch_budget(db_session):
    """The exact shape ticket 8028 was left in: blocked, budget spent."""
    ticket = _task(db_session)
    stage_key = ticket.workflow_stage_key
    for _ in range(5):
        record_stage_dispatch(db_session, ticket.id, stage_key)
    ticket.state = TicketState.BLOCKED
    ticket.blocking_issues = (
        f"Stage {stage_key!r} reached its retry budget of 5 dispatches "
        "without the workflow advancing past it."
    )
    db_session.add(ticket)
    db_session.commit()

    result = _call(
        db_session,
        "loregarden_requeue_ticket",
        {
            "ticket_id": ticket.id,
            "reason": "The gate flagged a file this ticket never touched.",
        },
    )

    assert result["blocking_issues"] == ""
    assert result["state"] != TicketState.BLOCKED.value
    assert count_stage_dispatches(db_session, ticket.id, stage_key) == 0


def test_requeue_records_why_the_budget_was_reset(db_session):
    """A counter that persists across runs must not be cleared anonymously."""
    from loregarden.models.domain import Artifact

    ticket = _task(db_session)
    _call(
        db_session,
        "loregarden_requeue_ticket",
        {"ticket_id": ticket.id, "reason": "Dependency landed on main."},
    )

    notes = db_session.exec(
        select(Artifact).where(
            Artifact.ticket_id == ticket.id,
            Artifact.kind == "context",
        )
    ).all()
    assert any("Dependency landed on main." in (note.content_json or "") for note in notes)


def test_requeue_demands_a_reason(db_session):
    with pytest.raises(ValueError, match="reason is required"):
        _call(
            db_session,
            "loregarden_requeue_ticket",
            {"ticket_id": _task(db_session).id, "reason": "   "},
        )


# --- supersede ---------------------------------------------------------------


def test_supersede_creates_the_replacement_and_closes_the_original(db_session):
    original = _new_subtree(db_session, "Ticket built on a wrong premise")[1]

    result = _call(
        db_session,
        "loregarden_supersede_ticket",
        {
            "ticket_id": original.id,
            "title": "Ticket that says what we meant",
            "description": "The corrected framing.",
            "acceptance_criteria": ["The replacement carries its own criteria."],
            "reason": "The premise was wrong, not incomplete.",
        },
    )

    replacement = db_session.get(Ticket, result["replacement"]["id"])
    assert replacement is not None
    assert replacement.title == "Ticket that says what we meant"
    assert replacement.workspace_id == original.workspace_id
    assert replacement.work_item_type == original.work_item_type
    assert load_criteria(replacement.acceptance_criteria_json) == [
        "The replacement carries its own criteria."
    ]

    db_session.refresh(original)
    assert original.state == TicketState.WONT_DO
    assert replacement.external_id in original.blocking_issues
    assert "The premise was wrong" in original.blocking_issues


def test_supersede_relates_the_two_so_the_trail_survives(db_session):
    original = _new_subtree(db_session, "Original with runs attached")[1]

    result = _call(
        db_session,
        "loregarden_supersede_ticket",
        {"ticket_id": original.id, "title": "Successor", "reason": "Rewritten."},
    )

    related = TicketRelationService(db_session).related(original.id)
    assert result["replacement"]["id"] in related


def test_supersede_keeps_the_replacement_under_the_same_parent(db_session):
    parent, _child = _new_subtree(db_session, "Owning capability")
    original = TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="Child to replace",
        work_item_type=WorkItemType.TASK,
        parent_ticket_id=parent.id,
    )

    result = _call(
        db_session,
        "loregarden_supersede_ticket",
        {"ticket_id": original.id, "title": "Better child", "reason": "Rescoped."},
    )

    replacement = db_session.get(Ticket, result["replacement"]["id"])
    assert replacement.parent_ticket_id == parent.id


def test_supersede_demands_a_reason(db_session):
    with pytest.raises(ValueError, match="reason is required"):
        _call(
            db_session,
            "loregarden_supersede_ticket",
            {"ticket_id": _task(db_session).id, "title": "Successor", "reason": ""},
        )


def test_stage_status_enum_is_offered_whole(db_session):
    """The tool advertises the real vocabulary, not a hand-copied subset."""
    from loregarden.mcp.tools import TOOL_DEFINITIONS

    schema = next(
        tool["inputSchema"]
        for tool in TOOL_DEFINITIONS
        if tool["name"] == "loregarden_set_ticket_workflow"
    )
    assert set(schema["properties"]["stage_status"]["enum"]) == {
        status.value for status in StageStatus
    }
