"""A pick the autopilot cannot start does not spend its slot.

The tick took `max_parallel` picks from the head of the order before it tried
any of them. A refused one used up the budget, and the next tick picked it
again; its refusal was logged once, so for hours the page read "on, idle" while
ready work sat behind it (pre-tinkerCg, 10-30 and 11-12).
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import patch

from loregarden.models.domain import AutopilotAction, AutopilotEvent, AutopilotUpdate, Ticket
from loregarden.services import initiative_autopilot
from loregarden.services.initiative_autopilot import run_autopilot, set_autopilot
from sqlmodel import Session, select
from tests.test_initiative_graph import (  # noqa: F401 - fixtures are used by name
    NOW,
    _initiative,
    _item,
    _milestone,
    _queued,
    queue_fixture,
    workspaces_fixture,
)


def _on(session: Session, initiative: Ticket, max_parallel: int) -> None:
    set_autopilot(
        session, initiative.id, AutopilotUpdate(enabled=True, max_parallel=max_parallel), actor="t"
    )


def _refuse_all_but(unmet, *startable: Ticket) -> None:
    """Every other ticket waits for a prerequisite that has not landed."""
    ids = {t.id for t in startable}
    landing = SimpleNamespace(
        ticket=SimpleNamespace(external_id="lg-prereq-1"),
        reason=SimpleNamespace(value="not_landed"),
        target="main",
    )
    unmet.side_effect = lambda _session, ticket, _workspace: [] if ticket.id in ids else [landing]


def _stalls(session: Session, initiative: Ticket) -> list[str]:
    return [
        e.detail
        for e in session.exec(
            select(AutopilotEvent).where(
                AutopilotEvent.initiative_id == initiative.id,
                AutopilotEvent.action == AutopilotAction.IDLE,
            )
        ).all()
    ]


def test_a_refused_pick_does_not_hold_the_slot_from_the_next(db_session, workspaces, queue):
    lanes, unmet = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _item(db_session, here, m, "a")
    _item(db_session, here, m, "b")
    last = _item(db_session, here, m, "z")
    _refuse_all_but(unmet, last)
    _on(db_session, initiative, max_parallel=1)

    assert run_autopilot(db_session, initiative.id, now=NOW) == 1

    assert _queued(lanes) == [last.id]


def test_it_still_starts_no_more_than_its_budget(db_session, workspaces, queue):
    lanes, unmet = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _item(db_session, here, m, "a")
    startable = [_item(db_session, here, m, name) for name in ("b", "c", "d")]
    _refuse_all_but(unmet, *startable)
    _on(db_session, initiative, max_parallel=2)

    assert run_autopilot(db_session, initiative.id, now=NOW) == 2

    assert set(_queued(lanes)) < {t.id for t in startable}


def test_it_stops_looking_after_enough_refusals(db_session, workspaces, queue):
    lanes, unmet = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    for name in ("a", "b", "c"):
        _item(db_session, here, m, name)
    last = _item(db_session, here, m, "z")
    _refuse_all_but(unmet, last)
    _on(db_session, initiative, max_parallel=1)

    with patch.object(initiative_autopilot, "MAX_REFUSALS_PER_TICK", 2):
        assert run_autopilot(db_session, initiative.id, now=NOW) == 0

    assert _queued(lanes) == []


def test_when_nothing_can_start_it_says_why_once(db_session, workspaces, queue):
    lanes, unmet = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    first = _item(db_session, here, m, "a")
    _item(db_session, here, m, "b")
    _refuse_all_but(unmet)
    _on(db_session, initiative, max_parallel=1)

    for _ in range(3):
        assert run_autopilot(db_session, initiative.id, now=NOW) == 0

    assert _queued(lanes) == []
    [stall] = _stalls(db_session, initiative)
    assert first.external_id in stall and "lg-prereq-1" in stall, stall
    assert "1 more refused" in stall, stall


def test_a_tick_that_starts_something_records_no_stall(db_session, workspaces, queue):
    _lanes, unmet = queue
    here, _ = workspaces
    initiative = _initiative(db_session)
    m = _milestone(db_session, here, "M", initiative)
    _item(db_session, here, m, "a")
    last = _item(db_session, here, m, "z")
    _refuse_all_but(unmet, last)
    _on(db_session, initiative, max_parallel=1)

    run_autopilot(db_session, initiative.id, now=NOW)

    assert _stalls(db_session, initiative) == []
