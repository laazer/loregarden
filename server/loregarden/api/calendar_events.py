"""Project runs and tickets into calendar events for chat primitives."""

from __future__ import annotations

from datetime import datetime, timezone

from fastapi import APIRouter, Depends, HTTPException, Query
from loregarden.db.session import get_session
from loregarden.models.domain import AgentRun, QueuedRun, Ticket, Workspace
from sqlmodel import Session, col, select

router = APIRouter(prefix="/workspaces", tags=["calendar"])


def _parse_iso(value: str | None) -> datetime | None:
    if not value:
        return None
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return None
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _iso(value: datetime | None) -> str | None:
    if value is None:
        return None
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.isoformat()


@router.get("/{slug}/calendar/events")
def list_calendar_events(
    slug: str,
    from_: str | None = Query(default=None, alias="from"),
    to: str | None = Query(default=None),
    session: Session = Depends(get_session),
) -> list[dict]:
    workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if not workspace:
        raise HTTPException(404, "Workspace not found")

    start = _parse_iso(from_)
    end = _parse_iso(to)
    events: list[dict] = []

    runs = session.exec(
        select(AgentRun)
        .where(AgentRun.workspace_id == workspace.id)
        .order_by(col(AgentRun.created_at).desc())
        .limit(200)
    ).all()
    for run in runs:
        when = run.started_at or run.created_at
        if start and when and when < start:
            continue
        if end and when and when > end:
            continue
        events.append(
            {
                "id": f"run-{run.id}",
                "title": f"{run.agent_id} · {run.stage_key or 'run'}",
                "starts_at": _iso(when),
                "ends_at": _iso(run.finished_at),
                "kind": "run",
                "ticket_id": run.ticket_id,
                "description": run.run_code,
            }
        )

    queued = session.exec(
        select(QueuedRun).where(QueuedRun.workspace_id == workspace.id).limit(100)
    ).all()
    for item in queued:
        when = item.created_at
        if start and when and when < start:
            continue
        if end and when and when > end:
            continue
        events.append(
            {
                "id": f"queue-{item.id}",
                "title": f"Queued · {item.ticket_id}",
                "starts_at": _iso(when),
                "ends_at": None,
                "kind": "scheduled",
                "ticket_id": item.ticket_id,
                "description": "Queued parallel run",
            }
        )

    tickets = session.exec(
        select(Ticket)
        .where(Ticket.workspace_id == workspace.id)
        .order_by(col(Ticket.updated_at).desc())
        .limit(100)
    ).all()
    for ticket in tickets:
        when = ticket.updated_at or ticket.created_at
        if start and when and when < start:
            continue
        if end and when and when > end:
            continue
        events.append(
            {
                "id": f"ticket-{ticket.id}",
                "title": ticket.title,
                "starts_at": _iso(when),
                "ends_at": None,
                "kind": "one_time",
                "ticket_id": ticket.id,
                "description": ticket.external_id,
            }
        )

    events.sort(key=lambda e: e.get("starts_at") or "")
    return events
