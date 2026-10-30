"""The autopilot checks the subtree, not only the row it picked.

lg-milestone-that-1420 was dispatched once a minute for hours. Its own
prerequisites were met, so the pre-dispatch check passed it; it carries no
stages of its own, and its only child was waiting on a ticket under another
parent. Every tick it cut a lane, the orchestrator found nothing to run, paused
in ~60 ms, and the pause was recorded SUCCEEDED — so the next tick picked it
again. `subtree_start_hold` reads that one layer earlier, before the slot.

The git half of readiness is replaced here, as `queue_fixture` does for the
picked ticket: these workspaces have no repository on disk, so a child's
prerequisites are judged by state (`unmet_for(..., None)`), which is the same
fallback the orchestrator takes when there is no repository to ask.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AutopilotEvent,
    AutopilotUpdate,
    StageStatus,
    Ticket,
    TicketState,
    WorkItemType,
)
from loregarden.services.child_readiness import has_work_left, subtree_start_hold, unmet_for
from loregarden.services.initiative_autopilot import run_autopilot, set_autopilot
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session, select
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    NOW,
    _edge,
    _initiative,
    _item,
    _milestone,
    _queued,
    queue_fixture,
    workspaces_fixture,
)


@pytest.fixture(autouse=True)
def _state_only_readiness():
    """Judge a child's prerequisites by state: there is no repository here."""
    with patch(
        "loregarden.services.child_readiness.unmet_for",
        side_effect=lambda session, child, _workspace: unmet_for(session, child, None),
    ):
        yield


def _on(session: Session, initiative: Ticket, max_parallel: int = 4) -> None:
    set_autopilot(
        session, initiative.id, AutopilotUpdate(enabled=True, max_parallel=max_parallel), actor="t"
    )


def _details(session: Session, ticket: Ticket) -> list[str]:
    return [
        e.detail
        for e in session.exec(
            select(AutopilotEvent).where(AutopilotEvent.ticket_id == ticket.id)
        ).all()
    ]


def _complete(session: Session, ticket: Ticket) -> None:
    """Walk every stage of ``ticket`` to DONE, the way a run would."""
    orch = OrchestrationService(session)
    instance, _created = orch.ensure_workflow_instance(ticket, commit=True)
    assert instance is not None
    _, stages = resolve_ticket_stages(session, ticket)
    for stage in stages:
        set_stage_status(ticket, instance, stages, stage.key, StageStatus.DONE)
    session.add(instance)
    session.commit()


def test_a_parent_whose_only_child_waits_is_not_dispatched(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent")
    child = _item(db_session, here, parent, "child")
    outside = _item(db_session, here, m, "outside")
    _edge(db_session, child, outside)
    _on(db_session, initiative)

    run_autopilot(db_session, initiative.id, now=NOW)

    queued = _queued(lanes)
    assert outside.id in queued, "the prerequisite itself is the work that can start"
    assert parent.id not in queued, "nothing under the parent can start"
    assert child.id not in queued


def test_the_hold_it_records_names_what_the_child_waits_for(db_session, workspaces, queue):
    _lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent")
    child = _item(db_session, here, parent, "child")
    outside = _item(db_session, here, m, "outside")
    _edge(db_session, child, outside)
    _on(db_session, initiative)

    run_autopilot(db_session, initiative.id, now=NOW)

    held = [d for d in _details(db_session, parent) if "nothing under it can start" in d]
    assert held, _details(db_session, parent)
    assert child.external_id in held[0]
    assert outside.external_id in held[0]


def test_it_is_recorded_once_while_it_stays_true(db_session, workspaces, queue):
    _lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent")
    child = _item(db_session, here, parent, "child")
    _edge(db_session, child, _item(db_session, here, m, "outside"))
    _on(db_session, initiative)

    for _ in range(3):
        run_autopilot(db_session, initiative.id, now=NOW)

    held = [d for d in _details(db_session, parent) if "nothing under it can start" in d]
    assert len(held) == 1


def test_a_parent_with_a_startable_child_is_not_held(db_session, workspaces, queue):
    """The guard holds nothing here.

    Asserted on the guard and on the absence of a hold rather than on the queue:
    `select_next` already refuses to pick a parent and its child in one tick
    (`_related`), and the child sorts first, so the parent's dispatch is not this
    guard's to grant.
    """
    _lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent")
    _item(db_session, here, parent, "child")
    _on(db_session, initiative)

    run_autopilot(db_session, initiative.id, now=NOW)

    assert subtree_start_hold(db_session, parent, None) == ""
    assert not [d for d in _details(db_session, parent) if "nothing under it can start" in d]


def test_a_parent_whose_children_are_done_is_dispatched_to_finalize(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent")
    child = _item(db_session, here, parent, "child", state=TicketState.DONE)
    _edge(db_session, child, _item(db_session, here, m, "outside"))
    _on(db_session, initiative)

    run_autopilot(db_session, initiative.id, now=NOW)

    assert parent.id in _queued(lanes), "its children are complete; the run finalizes it"


def test_a_parked_child_holds_its_parent_and_says_so(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent")
    _item(db_session, here, parent, "child", state=TicketState.PARKED)
    _on(db_session, initiative)

    run_autopilot(db_session, initiative.id, now=NOW)

    assert parent.id not in _queued(lanes)
    assert any("awaiting a person" in d for d in _details(db_session, parent))


def test_a_grandchild_that_can_run_makes_the_whole_subtree_startable(db_session, workspaces, queue):
    _lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent")
    child = _item(db_session, here, parent, "child")
    grandchild = _item(db_session, here, child, "grandchild")
    grandchild.work_item_type = WorkItemType.TASK
    db_session.add(grandchild)
    db_session.commit()
    _on(db_session, initiative)

    run_autopilot(db_session, initiative.id, now=NOW)

    assert subtree_start_hold(db_session, parent, None) == ""
    assert subtree_start_hold(db_session, child, None) == ""
    assert not [d for d in _details(db_session, parent) if "nothing under it can start" in d]


def test_a_grandchild_that_waits_holds_its_grandparent(db_session, workspaces, queue):
    lanes, _ = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    parent = _item(db_session, here, m, "parent")
    child = _item(db_session, here, parent, "child")
    grandchild = _item(db_session, here, child, "grandchild")
    grandchild.work_item_type = WorkItemType.TASK
    db_session.add(grandchild)
    db_session.commit()
    outside = _item(db_session, here, m, "outside")
    _edge(db_session, grandchild, outside)
    _on(db_session, initiative)

    run_autopilot(db_session, initiative.id, now=NOW)

    queued = _queued(lanes)
    assert outside.id in queued
    assert parent.id not in queued and child.id not in queued
    # `child` is the pick this tick (it sorts before `parent`, which `_related`
    # then takes off the table), so the hold is recorded against it.
    held = [d for d in _details(db_session, child) if "nothing under it can start" in d]
    assert held and grandchild.external_id in held[0]
    assert subtree_start_hold(db_session, parent, None) != ""


def test_a_child_with_no_workflow_instance_still_has_work_left(db_session, workspaces):
    """The driver's predicate reads a missing instance as complete; this must not.

    A child filed by a stage agent has no instance until a run ensures one. If
    that read as complete, the parent would look finished and be dispatched —
    the loop this guard exists to stop.
    """
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    child = _item(db_session, here, m, "child")

    assert OrchestrationService(db_session).get_workflow_instance(child.id) is None
    assert has_work_left(db_session, child) is True

    _complete(db_session, child)
    assert has_work_left(db_session, child) is False
