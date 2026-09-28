"""A monitor finding names its ticket, and says whether that ticket is still live."""

from loregarden.models.domain import (
    MonitorCondition,
    Ticket,
    TicketState,
    WorkItemType,
    Workspace,
)
from loregarden.models.domain.workflow_monitor import MonitorFindingView
from loregarden.services.monitor_finding_context import attach_ticket_context
from sqlmodel import Session, select


def _ticket(db_session: Session, external_id: str, state: TicketState) -> Ticket:
    workspace = db_session.exec(select(Workspace).where(Workspace.slug == "loregarden")).one()
    ticket = Ticket(
        external_id=external_id,
        workspace_id=workspace.id,
        title=f"Title of {external_id}",
        state=state,
        work_item_type=WorkItemType.TASK,
    )
    db_session.add(ticket)
    db_session.commit()
    db_session.refresh(ticket)
    return ticket


def _finding(ticket_id: str) -> MonitorFindingView:
    return MonitorFindingView(
        condition=MonitorCondition.STAGE_THRASH, ticket_id=ticket_id, summary="ran 6 times"
    )


def test_findings_carry_ticket_title_state_and_workspace(db_session: Session):
    live = _ticket(db_session, "ctx-live-1", TicketState.IN_PROGRESS)
    finished = _ticket(db_session, "ctx-done-2", TicketState.DONE)

    enriched = attach_ticket_context(db_session, [_finding(live.id), _finding(finished.id)])

    assert [
        (f.ticket_external_id, f.ticket_title, f.ticket_state, f.workspace_slug) for f in enriched
    ] == [
        ("ctx-live-1", "Title of ctx-live-1", TicketState.IN_PROGRESS, "loregarden"),
        ("ctx-done-2", "Title of ctx-done-2", TicketState.DONE, "loregarden"),
    ]


def test_workspace_scoped_and_vanished_tickets_keep_blank_context(db_session: Session):
    workspace_scoped = MonitorFindingView(
        condition=MonitorCondition.TIMEOUT_FLOOR_STALE, summary="floor is stale"
    )
    vanished = _finding("no-such-ticket")

    enriched = attach_ticket_context(db_session, [workspace_scoped, vanished])

    assert [(f.ticket_id, f.ticket_title, f.ticket_state) for f in enriched] == [
        ("", "", None),
        ("no-such-ticket", "", None),
    ]
