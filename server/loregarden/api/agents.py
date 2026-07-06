from fastapi import APIRouter
from loregarden.agents.registry import list_agents
from loregarden.skills.registry import list_skills

router = APIRouter(prefix="/agents", tags=["agents"])


@router.get("")
def get_agents() -> list[dict]:
    return list_agents()


@router.get("/skills")
def get_skills() -> list[str]:
    return list_skills()
