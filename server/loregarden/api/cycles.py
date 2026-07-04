from fastapi import APIRouter, Depends
from sqlmodel import Session, select

from loregarden.db.session import get_session
from loregarden.models.domain import Cycle, CycleStatus, Workspace
from pydantic import BaseModel

router = APIRouter(prefix="/cycles", tags=["cycles"])


class CycleSummary(BaseModel):
    id: str
    name: str
    status: CycleStatus
    goal: str
    workspace_slug: str
    ticket_count: int = 0


@router.get("", response_model=list[CycleSummary])
def list_cycles(
    *,
    workspace: str | None = None,
    session: Session = Depends(get_session),
) -> list[CycleSummary]:
    from loregarden.models.domain import Ticket

    query = select(Cycle)
    if workspace:
        ws = session.exec(select(Workspace).where(Workspace.slug == workspace)).first()
        if not ws:
            return []
        query = query.where(Cycle.workspace_id == ws.id)
    cycles = session.exec(query.order_by(Cycle.created_at)).all()
    result: list[CycleSummary] = []
    for cycle in cycles:
        ws = session.get(Workspace, cycle.workspace_id)
        count = len(
            session.exec(select(Ticket).where(Ticket.cycle_id == cycle.id)).all()
        )
        result.append(
            CycleSummary(
                id=cycle.id,
                name=cycle.name,
                status=cycle.status,
                goal=cycle.goal,
                workspace_slug=ws.slug if ws else "",
                ticket_count=count,
            )
        )
    return result
