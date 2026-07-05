from fastapi import APIRouter

from loregarden.services.memory_store import AgentMemoryService

router = APIRouter(prefix="/memory", tags=["memory"])


@router.get("/status")
def memory_status() -> dict:
    return AgentMemoryService.from_settings().status()
