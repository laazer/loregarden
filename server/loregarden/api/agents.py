from fastapi import APIRouter, Depends
from loregarden.db.session import get_session
from loregarden.services.studio_service import StudioService
from loregarden.skills.registry import list_skills
from sqlmodel import Session

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def get_agents(session: Session = Depends(get_session)) -> list[dict]:
    # DB is the source of truth; this returns the seeded built-ins plus any custom
    # agents, superseding the old registry-only listing.
    return [item.model_dump() for item in StudioService(session).list_agents()]


@router.get("/skills")
def get_skills() -> list[str]:
    return list_skills()
