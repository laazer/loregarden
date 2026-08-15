"""One writer per ticket state, decided by whether the ticket has children.

Two things derived `tickets.state` and neither knew about the other:

- `ticket_rollup.reconcile_ancestors` derives a parent from ITS CHILDREN, on a
  child's state change and at startup.
- `workflow_state.reconcile_workflow_state` derives a ticket from ITS OWN
  STAGES, on every `GET /api/tickets/{id}` and every stage-map build.

A parent has no stages anyone runs — its instance sits at `triage/pending`
forever — so the stage-derived answer for a parent is `backlog` in perpetuity.
Whichever writer ran last won, and the one that ran most often was the wrong
one. Reproduced against the live server before the fix:

    set child -> in_progress (rolls the milestone up)   milestone: in_progress
    GET the milestone                                   milestone: backlog

A parent was briefly correct after a child moved and silently wrong again as
soon as anyone opened it, which is the parent/child status drift the track-e
audit was opened to explain.

These build real tickets through `TicketService` rather than hand-rolled rows:
`reconcile_ticket` returns early when a ticket has no workflow instance, so a
bare `Ticket()` would make every assertion here pass without exercising
anything.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import Ticket, TicketState, WorkItemType
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_rollup import has_children, reconcile_ancestors
from loregarden.services.ticket_service import TicketService


def _subtree(session) -> tuple[Ticket, Ticket, Ticket]:
    """A capability with two tasks under it, all carrying real workflows.

    Two children rather than one so the parent can sit at `in_progress`, which
    is the state the clobber actually destroyed. A `done` parent was already
    protected by the sticky-done guard in `reconcile_workflow_state`, so a test
    built only on `done` passes with or without the fix.
    """
    svc = TicketService(session)
    milestone = svc.create_ticket(
        workspace_slug="loregarden",
        title="parent state owner — milestone",
        work_item_type=WorkItemType.MILESTONE,
    )
    feature = svc.create_ticket(
        workspace_slug="loregarden",
        title="parent state owner — feature",
        work_item_type=WorkItemType.FEATURE,
        parent_ticket_id=milestone.id,
    )
    capability = svc.create_ticket(
        workspace_slug="loregarden",
        title="parent state owner — capability",
        work_item_type=WorkItemType.CAPABILITY,
        parent_ticket_id=feature.id,
    )
    task = svc.create_ticket(
        workspace_slug="loregarden",
        title="parent state owner — task",
        work_item_type=WorkItemType.TASK,
        parent_ticket_id=capability.id,
    )
    sibling = svc.create_ticket(
        workspace_slug="loregarden",
        title="parent state owner — sibling task",
        work_item_type=WorkItemType.TASK,
        parent_ticket_id=capability.id,
    )
    return capability, task, sibling


def _set_child_state(session, parent: Ticket, child: Ticket, state: TicketState) -> None:
    child.state = state
    session.add(child)
    session.commit()
    reconcile_ancestors(session, child)
    session.refresh(parent)


@pytest.fixture(name="instances_exist")
def instances_exist_fixture(db_session):
    """Guards the whole module against the vacuous-test failure above."""
    capability, task, sibling = _subtree(db_session)
    svc = OrchestrationService(db_session)
    for ticket in (capability, task, sibling):
        instance, _ = svc.ensure_workflow_instance(ticket, commit=True)
        _, stages = svc._resolve_stages(ticket)
        assert instance is not None and stages, (
            f"{ticket.external_id} has no workflow to derive from"
        )
    return capability, task, sibling


# ---- a parent's state survives being read ------------------------------


def test_a_partly_finished_parent_stays_in_progress_across_reads(db_session, instances_exist):
    """The exact clobber, at the call every ticket read passes through.

    One child done, one not: the parent is `in_progress`, and `in_progress` is
    what the stage-derived `backlog` used to overwrite. `done` is the wrong
    state to test with — the sticky-done guard already refused that one, which
    is why the bug survived so long.
    """
    capability, task, _sibling = instances_exist
    _set_child_state(db_session, capability, task, TicketState.DONE)
    assert capability.state == TicketState.IN_PROGRESS

    for _ in range(3):
        OrchestrationService(db_session).reconcile_ticket(capability)
        db_session.refresh(capability)

    assert capability.state == TicketState.IN_PROGRESS


def test_a_finished_parent_stays_finished_across_reads(db_session, instances_exist):
    """The same guarantee for a fully resolved subtree."""
    capability, task, sibling = instances_exist
    _set_child_state(db_session, capability, task, TicketState.DONE)
    _set_child_state(db_session, capability, sibling, TicketState.DONE)
    assert capability.state == TicketState.DONE

    for _ in range(3):
        OrchestrationService(db_session).reconcile_ticket(capability)
        db_session.refresh(capability)

    assert capability.state == TicketState.DONE


def test_building_a_parents_stage_map_does_not_move_it(db_session, instances_exist):
    """The stage map is drawn for parents too — and drew the state as a side effect.

    `build_stage_views` reconciles before rendering, so the board and the MCP
    ticket payload were both write paths onto a parent's state.
    """
    capability, task, _sibling = instances_exist
    _set_child_state(db_session, capability, task, TicketState.DONE)
    assert capability.state == TicketState.IN_PROGRESS

    OrchestrationService(db_session).build_stage_views(capability)
    db_session.refresh(capability)

    assert capability.state == TicketState.IN_PROGRESS


# ---- a read repairs rather than merely declining to corrupt ------------


def test_reading_a_parent_corrects_a_stale_rollup(db_session, instances_exist):
    """The push-on-change hook and the startup sweep both leave windows.

    A child created rather than moved does not push, and a server that has not
    restarted has not swept. A read is the one thing that reliably happens.
    """
    capability, task, sibling = instances_exist
    _set_child_state(db_session, capability, task, TicketState.DONE)
    _set_child_state(db_session, capability, sibling, TicketState.DONE)

    capability.state = TicketState.BACKLOG  # as if a hook was missed
    db_session.add(capability)
    db_session.commit()

    OrchestrationService(db_session).reconcile_ticket(capability)
    db_session.refresh(capability)

    assert capability.state == TicketState.DONE


# ---- leaves are untouched ----------------------------------------------


def test_a_leaf_still_derives_its_state_from_its_own_stages(db_session, instances_exist):
    """The fix must not disable workflow derivation for tickets that run stages.

    A childless ticket is what `reconcile_workflow_state` was written for, and
    it keeps owning its own state: its stages are all pending, so the workflow
    answers `backlog` and is allowed to say so.
    """
    _capability, task, _sibling = instances_exist
    assert has_children(db_session, task.id) is False

    task.state = TicketState.IN_PROGRESS
    db_session.add(task)
    db_session.commit()

    OrchestrationService(db_session).reconcile_ticket(task)
    db_session.refresh(task)

    assert task.state == TicketState.BACKLOG


def test_gaining_a_child_moves_ownership(db_session, instances_exist):
    """Parenthood is behavioural, not a work_item_type.

    A capability that grows tasks stops answering for itself the moment it has
    them, which keeps the rule from depending on how a ticket was typed.
    """
    capability, _task, _sibling = instances_exist
    assert has_children(db_session, capability.id) is True

    fresh = TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="parent state owner — childless task",
        work_item_type=WorkItemType.TASK,
        parent_ticket_id=capability.id,
    )
    assert has_children(db_session, fresh.id) is False
