"""The 12-criterion cap holds on every writer, and refuses growth rather than size.

78 live tickets were already over the cap when it landed (2026-10-21), 33 of them
last written by pipeline agents, so the guard refuses a write that grows a list
past 12 and lets a list already longer be edited or shortened.
"""

from __future__ import annotations

from types import SimpleNamespace

import pytest
from loregarden.models.domain import UpdateTicketRequest, WorkItemType, Workspace
from loregarden.services.acceptance_criteria import (
    MAX_ACCEPTANCE_CRITERIA,
    AcceptanceCriteriaCapError,
    guard_criteria_growth,
    load_criteria,
    serialize_criteria,
)
from loregarden.services.finalize_hierarchy import _build_ticket
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.seed import seed_database
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select
from tests.factories import make_ticket


def _criteria(n: int) -> list[str]:
    return [f"criterion {i}" for i in range(n)]


@pytest.fixture(name="ws")
def ws_fixture(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ws = session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
        milestone = make_ticket(
            session,
            workspace_id=ws.id,
            external_id="proj-m-1",
            work_item_type=WorkItemType.MILESTONE,
        )
        return SimpleNamespace(id=ws.id, slug=ws.slug, milestone_id=milestone.id)


def test_guard_refuses_growth_past_the_cap_and_nothing_else():
    guard_criteria_growth(_criteria(11), _criteria(12))
    guard_criteria_growth(_criteria(20), _criteria(20))
    guard_criteria_growth(_criteria(20), _criteria(14))
    with pytest.raises(AcceptanceCriteriaCapError, match="from 12 to 13"):
        guard_criteria_growth(_criteria(12), _criteria(13))
    with pytest.raises(AcceptanceCriteriaCapError):
        guard_criteria_growth(_criteria(20), _criteria(21))


@pytest.mark.parametrize(
    "count, refused", [(MAX_ACCEPTANCE_CRITERIA, False), (MAX_ACCEPTANCE_CRITERIA + 1, True)]
)
def test_create_ticket_path(isolated_db, ws, count, refused):
    with Session(isolated_db) as session:

        def create():
            return TicketService(session).create_ticket(
                workspace_slug=ws.slug,
                title="capped",
                work_item_type=WorkItemType.FEATURE,
                parent_ticket_id=ws.milestone_id,
                acceptance_criteria=_criteria(count),
            )

        if refused:
            with pytest.raises(AcceptanceCriteriaCapError):
                create()
        else:
            assert len(load_criteria(create().acceptance_criteria_json)) == count


def test_manual_edit_path_covers_mcp_and_patch(isolated_db, ws):
    """MCP update_ticket and REST PATCH both reach update_ticket_manual."""
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-1", parent_ticket_id=ws.milestone_id
        )
        orch = OrchestrationService(session)
        orch.update_ticket_manual(ticket, UpdateTicketRequest(acceptance_criteria=_criteria(12)))
        with pytest.raises(AcceptanceCriteriaCapError):
            orch.update_ticket_manual(
                ticket, UpdateTicketRequest(acceptance_criteria=_criteria(13))
            )
        session.refresh(ticket)
        assert len(load_criteria(ticket.acceptance_criteria_json)) == 12


def test_a_ticket_already_over_the_cap_stays_editable(isolated_db, ws):
    with Session(isolated_db) as session:
        ticket = make_ticket(
            session, workspace_id=ws.id, external_id="proj-2", parent_ticket_id=ws.milestone_id
        )
        ticket.acceptance_criteria_json = serialize_criteria(_criteria(20))
        session.add(ticket)
        session.commit()
        orch = OrchestrationService(session)
        reworded = [*_criteria(19), "criterion 19, reworded"]
        orch.update_ticket_manual(ticket, UpdateTicketRequest(acceptance_criteria=reworded))
        orch.update_ticket_manual(ticket, UpdateTicketRequest(acceptance_criteria=_criteria(15)))
        with pytest.raises(AcceptanceCriteriaCapError):
            orch.update_ticket_manual(
                ticket, UpdateTicketRequest(acceptance_criteria=_criteria(16))
            )


def test_finalize_hierarchy_path(isolated_db, ws):
    with Session(isolated_db) as session:
        workspace = session.get(Workspace, ws.id)
        item = SimpleNamespace(
            external_id="proj-final-1",
            work_item_type=WorkItemType.FEATURE,
            acceptance_criteria=_criteria(13),
            description="",
            priority=3,
        )
        with pytest.raises(AcceptanceCriteriaCapError):
            _build_ticket(session, item, ws=workspace, parent_id=ws.milestone_id, title="t")
