"""A blocked ticket can be deferred so its dependents proceed; the autopilot sweeps deferred work."""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from loregarden.agents.stage_context import build_orchestration_context
from loregarden.mcp.tools import execute_tool
from loregarden.models.domain import (
    AgentRun,
    AutopilotAction,
    AutopilotEvent,
    AutopilotUpdate,
    DomainEvent,
    EventType,
    Ticket,
    TicketState,
    WorkItemType,
    utcnow,
)
from loregarden.services.deferral_state import is_deferred
from loregarden.services.deferred_sweep import MAX_DEFERRED_SWEEPS
from loregarden.services.dependency_readiness import unmet_prerequisites_for_start
from loregarden.services.initiative_autopilot import run_autopilot, set_autopilot
from loregarden.services.initiative_graph import build_plan
from loregarden.services.initiative_service import load_plan_root
from loregarden.services.orchestrator_decisions import decisions_of
from loregarden.services.ticket_deferral import (
    DeferralRefused,
    defer_ticket,
    undefer_ticket,
)
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.ticket_rollup import DEFERRED_NOTE, reconcile_parent
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    NOW,
    _queued,
    queue_fixture,
    workspaces_fixture,
)


@pytest.fixture
def tree(db_session: Session, workspaces):
    """A milestone with a blocked child, and a sibling that waits on it."""
    here, _ = workspaces
    service = TicketService(db_session)
    root = service.create_ticket(
        workspace_slug=here.slug, title="Root", work_item_type=WorkItemType.MILESTONE
    )
    blocker = service.create_ticket(
        workspace_slug=here.slug,
        title="Blocker",
        parent_ticket_id=root.id,
        work_item_type=WorkItemType.FEATURE,
    )
    dependent = service.create_ticket(
        workspace_slug=here.slug,
        title="Dependent",
        parent_ticket_id=root.id,
        work_item_type=WorkItemType.FEATURE,
    )
    blocker.state = TicketState.BLOCKED
    db_session.add(blocker)
    db_session.commit()
    TicketDependencyService(db_session).add_dependency(dependent.id, blocker.id, created_by="t")
    return root, blocker, dependent, here


REASON = "waits on a vendor fix nobody here can make"


# ---- deferring ------------------------------------------------------------


def test_only_a_blocked_ticket_with_a_reason_can_be_deferred(db_session, tree):
    _, blocker, dependent, _ = tree
    with pytest.raises(DeferralRefused, match="not blocked"):
        defer_ticket(db_session, dependent, REASON, actor="t")
    with pytest.raises(DeferralRefused, match="needs a reason"):
        defer_ticket(db_session, blocker, "  ", actor="t")
    assert not is_deferred(blocker)


def test_a_deferred_ticket_stays_blocked_and_is_on_the_history(db_session, tree):
    _, blocker, dependent, _ = tree
    defer_ticket(db_session, blocker, REASON, actor="operator")

    db_session.refresh(blocker)
    assert blocker.state is TicketState.BLOCKED and is_deferred(blocker)
    [(_, payload)] = [
        d for d in decisions_of(db_session, [blocker.id]) if d[1]["decision"] == "ticket_deferred"
    ]
    assert REASON in payload["reason"] and payload["dependents"] == [dependent.id]


def test_a_resolved_ticket_is_no_longer_deferred(db_session, tree):
    _, blocker, _, _ = tree
    defer_ticket(db_session, blocker, REASON, actor="t")
    blocker.state = TicketState.DONE
    assert not is_deferred(blocker)


# ---- its dependents proceed ----------------------------------------------


def test_a_deferred_prerequisite_holds_nothing(db_session, tree):
    _, blocker, dependent, here = tree
    deps = TicketDependencyService(db_session)
    assert [t.id for t in deps.unmet_prerequisites(dependent.id)] == [blocker.id]
    assert [u.ticket.id for u in unmet_prerequisites_for_start(db_session, dependent, here)] == [
        blocker.id
    ]

    defer_ticket(db_session, blocker, REASON, actor="t")

    assert deps.unmet_prerequisites(dependent.id) == []
    assert unmet_prerequisites_for_start(db_session, dependent, here) == []


def test_the_plan_graph_reads_a_dependent_of_a_deferred_ticket_as_ready(db_session, tree):
    root, blocker, dependent, _ = tree
    graph = build_plan(db_session, load_plan_root(db_session, root.id), now=NOW).graph
    assert graph.nodes[dependent.id].waiting_on == [blocker.id]

    defer_ticket(db_session, blocker, REASON, actor="t")

    graph = build_plan(db_session, load_plan_root(db_session, root.id), now=NOW).graph
    assert graph.nodes[dependent.id].waiting_on == []


def _context(db_session: Session, ticket: Ticket) -> str:
    run = AgentRun(
        run_code=f"ctx-{ticket.external_id}",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="backend_implementer",
        stage_key="implement",
    )
    return build_orchestration_context(ticket=ticket, run=run, stage_def=None, session=db_session)


def test_a_dependents_brief_names_the_deferred_prerequisite_and_why(db_session, tree):
    _, blocker, dependent, _ = tree
    assert "Deferred prerequisites" not in _context(db_session, dependent)

    defer_ticket(db_session, blocker, REASON, actor="t")

    context = _context(db_session, dependent)
    assert "## Deferred prerequisites you proceed without" in context
    assert f"{blocker.external_id} — Blocker: {REASON}" in context


# ---- its parent cannot finish --------------------------------------------


def test_a_parent_with_a_deferred_child_is_blocked_and_says_which_and_why(db_session, tree):
    root, blocker, dependent, _ = tree
    defer_ticket(db_session, blocker, REASON, actor="t")
    dependent.state = TicketState.DONE
    db_session.add(dependent)
    db_session.commit()

    reconcile_parent(db_session, root)

    assert root.state is TicketState.BLOCKED
    assert root.blocking_issues == f"{DEFERRED_NOTE}{blocker.external_id} ({REASON})"


def test_taking_the_deferral_back_clears_the_parents_note(db_session, tree):
    root, blocker, _, _ = tree
    defer_ticket(db_session, blocker, REASON, actor="t")
    db_session.refresh(root)
    assert root.blocking_issues.startswith(DEFERRED_NOTE)

    undefer_ticket(db_session, blocker, actor="t")

    db_session.refresh(root)
    assert root.blocking_issues == ""
    assert TicketDependencyService(db_session).unmet_prerequisites(tree[2].id) != []


# ---- sweeps ----------------------------------------------------------------


def _events(db_session: Session, root: Ticket, action: AutopilotAction) -> list[AutopilotEvent]:
    return list(
        db_session.exec(
            select(AutopilotEvent).where(
                AutopilotEvent.initiative_id == root.id, AutopilotEvent.action == action
            )
        ).all()
    )


def _tick(db_session: Session, root: Ticket) -> int:
    with patch("loregarden.services.initiative_autopilot.prepare_rerun", return_value=""):
        return run_autopilot(db_session, root.id, now=NOW)


@pytest.fixture
def swept(db_session, tree, queue):
    """The tree with the blocker deferred, its dependent done, and autopilot on."""
    root, blocker, dependent, _ = tree
    defer_ticket(db_session, blocker, REASON, actor="t")
    dependent.state = TicketState.DONE
    db_session.add(dependent)
    db_session.commit()
    set_autopilot(db_session, root.id, AutopilotUpdate(enabled=True), actor="t")
    return root, blocker, queue[0]


def _back_to_blocked(db_session: Session, ticket: Ticket) -> None:
    db_session.refresh(ticket)
    ticket.state = TicketState.BLOCKED
    db_session.add(ticket)
    db_session.commit()


def test_with_nothing_else_to_start_a_sweep_retries_the_deferred_ticket(db_session, swept):
    root, blocker, lanes = swept

    assert _tick(db_session, root) == 1

    assert _queued(lanes) == [blocker.id]
    [started] = _events(db_session, root, AutopilotAction.SWEEP_STARTED)
    assert "sweep 1 of at most" in started.detail and blocker.external_id in started.detail
    assert [e.ticket_id for e in _events(db_session, root, AutopilotAction.SWEEP_RETRIED)] == [
        blocker.id
    ]
    assert _events(db_session, root, AutopilotAction.RETRIED) == []


def test_a_sweep_that_finished_nothing_ends_the_sweeps_and_lists_what_remains(db_session, swept):
    root, blocker, lanes = swept
    _tick(db_session, root)
    _back_to_blocked(db_session, blocker)

    assert _tick(db_session, root) == 0
    assert _tick(db_session, root) == 0

    [ended] = _events(db_session, root, AutopilotAction.SWEEPS_ENDED)
    assert "moved none" in ended.detail and blocker.external_id in ended.detail
    assert _queued(lanes) == [blocker.id]


def test_a_sweep_that_finished_something_starts_the_next_until_the_bound(db_session, tree, queue):
    root, blocker, dependent, here = tree
    service = TicketService(db_session)
    extra = []
    for n in range(MAX_DEFERRED_SWEEPS + 1):
        ticket = service.create_ticket(
            workspace_slug=here.slug,
            title=f"deferred {n}",
            parent_ticket_id=root.id,
            work_item_type=WorkItemType.FEATURE,
        )
        ticket.state = TicketState.BLOCKED
        db_session.add(ticket)
        db_session.commit()
        defer_ticket(db_session, ticket, REASON, actor="t")
        extra.append(ticket)
    for ticket in (blocker, dependent):
        ticket.state = TicketState.DONE
        db_session.add(ticket)
    db_session.commit()
    set_autopilot(db_session, root.id, AutopilotUpdate(enabled=True), actor="t")

    for sweep in range(MAX_DEFERRED_SWEEPS):
        assert _tick(db_session, root) > 0, f"sweep {sweep + 1} did not start"
        # One ticket of the sweep finishes; the rest block again.
        for ticket in extra:
            _back_to_blocked(db_session, ticket)
        extra[sweep].state = TicketState.DONE
        db_session.add(extra[sweep])
        db_session.commit()

    assert _tick(db_session, root) == 0
    [ended] = _events(db_session, root, AutopilotAction.SWEEPS_ENDED)
    assert "the bound" in ended.detail and extra[-1].external_id in ended.detail
    assert len(_events(db_session, root, AutopilotAction.SWEEP_STARTED)) == MAX_DEFERRED_SWEEPS


def test_a_deferred_ticket_with_a_repair_turn_waits_for_the_sweep(db_session, tree, queue):
    """The ordinary retry would take it at once; a deferral means after other work settles."""
    root, blocker, dependent, _ = tree
    defer_ticket(db_session, blocker, REASON, actor="t")
    set_autopilot(db_session, root.id, AutopilotUpdate(enabled=True), actor="t")
    with patch(
        "loregarden.services.initiative_autopilot.retry_reason", return_value="repair unspent"
    ):
        _tick(db_session, root)

    assert _queued(queue[0]) == [dependent.id]
    assert _events(db_session, root, AutopilotAction.RETRIED) == []


def test_no_sweep_while_a_retried_ticket_is_in_flight(db_session, swept):
    root, blocker, _ = swept
    _tick(db_session, root)
    with patch(
        "loregarden.services.initiative_graph.classify_ticket_activity",
        return_value={blocker.id: _running()},
    ):
        assert _tick(db_session, root) == 0
    assert _events(db_session, root, AutopilotAction.SWEEPS_ENDED) == []


def _running():
    from loregarden.services.ticket_activity import TicketActivity

    return TicketActivity.RUNNING


# ---- a retry's brief -------------------------------------------------------


def test_a_retried_deferred_tickets_brief_says_what_landed_since(db_session, tree):
    _, blocker, dependent, _ = tree
    landed_before = TicketService(db_session).create_ticket(
        workspace_slug=tree[3].slug,
        title="Landed before",
        parent_ticket_id=tree[0].id,
        work_item_type=WorkItemType.FEATURE,
    )
    db_session.add(
        DomainEvent(
            type=EventType.TICKET_LANDED,
            workspace_id=dependent.workspace_id,
            ticket_id=landed_before.id,
            payload_json="{}",
            created_at=utcnow(),
        )
    )
    db_session.commit()
    defer_ticket(db_session, blocker, REASON, actor="t")
    db_session.add(
        DomainEvent(
            type=EventType.TICKET_LANDED,
            workspace_id=dependent.workspace_id,
            ticket_id=dependent.id,
            payload_json="{}",
            created_at=utcnow(),
        )
    )
    db_session.commit()

    context = _context(db_session, blocker)

    assert "## This ticket was deferred" in context and REASON in context
    assert "Landed since it was deferred (1):" in context
    assert f"{dependent.external_id} — Dependent" in context
    assert landed_before.external_id not in context


def test_a_deferred_ticket_with_nothing_landed_says_so(db_session, tree):
    _, blocker, _, _ = tree
    defer_ticket(db_session, blocker, REASON, actor="t")
    assert "Nothing has landed in this workspace since it was deferred." in _context(
        db_session, blocker
    )


# ---- the tool ---------------------------------------------------------------


def test_the_tool_defers_and_takes_it_back(db_session, tree):
    _, blocker, _, _ = tree
    deferred = json.loads(
        execute_tool(
            db_session,
            "loregarden_defer_ticket",
            {"ticket_id": blocker.external_id, "reason": REASON},
        )
    )
    assert deferred["deferred"] is True and deferred["reason"] == REASON

    undone = json.loads(
        execute_tool(
            db_session, "loregarden_defer_ticket", {"ticket_id": blocker.id, "defer": False}
        )
    )
    assert undone["deferred"] is False
