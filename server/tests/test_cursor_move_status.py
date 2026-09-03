"""Moving the workflow cursor must not claim a stage finished.

`set_ticket_workflow` with `stage_key` and no `stage_status` documented itself as
"just becomes the current stage". It did not: the stage the cursor landed on
inherited whatever the *previous* cursor was showing, so moving off a `done`
stage marked the target done — a stage that had never run
(lg-workflow-integrity-659).

Found during recovery from the slot leak in lg-workflow-integrity-568, where
repairing one incident silently fabricated a completed stage.

Why this matters more than an ordinary bug: a silent failure leaves work undone,
which someone eventually notices. This left work *claimed* — the stage becomes
indistinguishable from one that ran and passed, its gate never fired, and every
later reader (the workflow pane, a rollup, a reviewer deciding what is left) is
reading a fact that was invented by a cursor move.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    StageStatus,
    Ticket,
    UpdateTicketRequest,
    WorkItemType,
    Workspace,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session, select


@pytest.fixture(name="ticket")
def ticket_fixture(client, isolated_db) -> str:
    """A ticket on a real workflow, with its first stage finished."""
    with Session(isolated_db) as session:
        workspace = session.exec(select(Workspace)).first()
        milestone = session.exec(
            select(Ticket).where(Ticket.work_item_type == WorkItemType.MILESTONE)
        ).first()
        created = TicketService(session).create_ticket(
            workspace_slug=workspace.slug,
            title="Cursor move target",
            work_item_type=WorkItemType.BUG,
            parent_ticket_id=milestone.id,
        )
        session.commit()
        return created.id


def _stages(session: Session, ticket_id: str) -> list:
    ticket = session.get(Ticket, ticket_id)
    return OrchestrationService(session).stage_views_for_read(ticket)


def _status_of(session: Session, ticket_id: str, stage_key: str) -> StageStatus:
    return next(v.status for v in _stages(session, ticket_id) if v.key == stage_key)


def test_moving_the_cursor_does_not_mark_the_target_done(isolated_db, ticket):
    """AC1 and AC4 — the incident shape, from a `done` stage onto an unrun one."""
    with Session(isolated_db) as session:
        orch = OrchestrationService(session)
        row = session.get(Ticket, ticket)
        stages = _stages(session, ticket)
        assert len(stages) >= 2, "need two stages to move between"
        first, target = stages[0].key, stages[1].key

        # Finish the first stage for real.
        orch.update_ticket_manual(
            row,
            UpdateTicketRequest(stage_key=first, stage_status=StageStatus.DONE),
        )
        session.commit()
        assert _status_of(session, ticket, first) is StageStatus.DONE

        # Now just move the cursor — no status.
        orch.update_ticket_manual(row, UpdateTicketRequest(workflow_stage_key=target))
        session.commit()

        assert row.workflow_stage_key == target, "the cursor did not move"
        assert _status_of(session, ticket, target) is not StageStatus.DONE, (
            "moving the cursor marked an unrun stage done"
        )
        # And the stage that really did finish is untouched.
        assert _status_of(session, ticket, first) is StageStatus.DONE


def test_the_target_keeps_the_status_it_already_had(isolated_db, ticket):
    """ "Just becomes the current stage" means exactly that: whatever status the
    target already carried survives the move.

    Set up so the answer differs between the two behaviours — the cursor is
    showing `done` while the target is `wont_do`, so inheriting and keeping give
    different results. An earlier version of this test used `blocked` for the
    target, which is also what the cursor derived, so it passed whether the fix
    was present or not. A test that cannot fail is not evidence.
    """
    with Session(isolated_db) as session:
        orch = OrchestrationService(session)
        row = session.get(Ticket, ticket)
        stages = _stages(session, ticket)
        first, target = stages[0].key, stages[1].key

        orch.update_ticket_manual(
            row, UpdateTicketRequest(stage_key=target, stage_status=StageStatus.WONT_DO)
        )
        session.commit()
        orch.update_ticket_manual(
            row, UpdateTicketRequest(stage_key=first, stage_status=StageStatus.DONE)
        )
        session.commit()

        before = _status_of(session, ticket, target)
        assert before is StageStatus.WONT_DO, "fixture did not set the target up"
        assert row.workflow_stage_status is not StageStatus.WONT_DO, (
            "cursor status matches the target, so this test cannot discriminate"
        )

        orch.update_ticket_manual(row, UpdateTicketRequest(workflow_stage_key=target))
        session.commit()

        assert _status_of(session, ticket, target) is before


def test_an_explicit_status_still_wins(isolated_db, ticket):
    """The documented recovery from a bad cursor move restores a real outcome by
    naming it. That has to keep working — the fix is about the silent path."""
    with Session(isolated_db) as session:
        orch = OrchestrationService(session)
        row = session.get(Ticket, ticket)
        target = _stages(session, ticket)[1].key

        orch.update_ticket_manual(
            row, UpdateTicketRequest(stage_key=target, stage_status=StageStatus.DONE)
        )
        session.commit()

        assert _status_of(session, ticket, target) is StageStatus.DONE


def test_through_the_mcp_tool_as_an_operator_would(client, isolated_db, ticket):
    """AC3, on the path the incident actually took: the MCP tool, not the
    service."""
    with Session(isolated_db) as session:
        stages = _stages(session, ticket)
        first, target = stages[0].key, stages[1].key

    def call(args: dict) -> dict:
        response = client.post(
            "/mcp",
            json={
                "jsonrpc": "2.0",
                "id": 1,
                "method": "tools/call",
                "params": {"name": "loregarden_set_ticket_workflow", "arguments": args},
            },
        )
        assert response.status_code == 200, response.text
        return response.json()

    call({"ticket_id": ticket, "stage_key": first, "stage_status": "done"})
    call({"ticket_id": ticket, "stage_key": target})

    with Session(isolated_db) as session:
        assert _status_of(session, ticket, target) is not StageStatus.DONE
        assert _status_of(session, ticket, first) is StageStatus.DONE


def test_the_schema_says_what_the_move_does(client):
    """AC2. The description claimed "just becomes the current stage" while the
    code marked it done; whichever changes, they must not disagree again."""
    response = client.post(
        "/mcp", json={"jsonrpc": "2.0", "id": 1, "method": "tools/list", "params": {}}
    )
    tools = {t["name"]: t for t in response.json()["result"]["tools"]}
    stage_key = tools["loregarden_set_ticket_workflow"]["inputSchema"]["properties"]["stage_key"][
        "description"
    ]

    assert "keeps whatever status it already had" in stage_key
    assert "does not mark the target complete" in stage_key
