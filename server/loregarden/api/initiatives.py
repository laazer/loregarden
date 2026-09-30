"""Cross-workspace reads for initiatives.

Writes are the ticket endpoints': create with `work_item_type=initiative` and no
workspace, attach or detach a milestone with `PATCH /tickets/{id}`
`parent_ticket_id`, delete with `DELETE /tickets/{id}`.
"""

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import InitiativeMilestoneView, InitiativeView
from loregarden.services.initiative_service import (
    attachable_milestones,
    get_initiative,
    list_initiatives,
)
from sqlmodel import Session

router = APIRouter(prefix="/initiatives", tags=["initiatives"])


@router.get("", response_model=list[InitiativeView])
def list_initiatives_endpoint(session: Session = Depends(get_session)) -> list[InitiativeView]:
    return list_initiatives(session)


@router.get("/attachable-milestones", response_model=list[InitiativeMilestoneView])
def attachable_milestones_endpoint(
    session: Session = Depends(get_session),
) -> list[InitiativeMilestoneView]:
    return attachable_milestones(session)


@router.get("/{initiative_id}", response_model=InitiativeView)
def get_initiative_endpoint(
    initiative_id: str, session: Session = Depends(get_session)
) -> InitiativeView:
    try:
        return get_initiative(session, initiative_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
