from fastapi import APIRouter, HTTPException
from loregarden.services.memory_config import (
    apply_memory_config,
    current_memory_config,
    memory_config_defaults,
)
from loregarden.services.memory_store import AgentMemoryService
from pydantic import BaseModel, Field

router = APIRouter(prefix="/memory", tags=["memory"])


class MemoryConfigBody(BaseModel):
    icloud_root: str = ""
    obsidian_vault_dir: str = ""
    obsidian_memory_subdir: str = Field(default="Loregarden/Memory", min_length=1)
    obsidian_learnings_subdir: str = Field(default="Loregarden/Learnings", min_length=1)
    obsidian_blogposts_subdir: str = Field(default="Loregarden/BlogPosts", min_length=1)
    obsidian_checkpoints_subdir: str = Field(default="Loregarden/Checkpoints", min_length=1)
    memory_sqlite_url: str = ""
    database_url: str = ""


def _memory_config_response() -> dict:
    service = AgentMemoryService.from_settings()
    return {
        "config": current_memory_config(),
        "status": service.status(),
        "defaults": memory_config_defaults(),
    }


@router.get("/status")
def memory_status(workspace_slug: str = "") -> dict:
    return AgentMemoryService.from_settings().status(workspace_slug=workspace_slug)


@router.get("/config")
def get_memory_config() -> dict:
    return _memory_config_response()


@router.put("/config")
def put_memory_config(body: MemoryConfigBody) -> dict:
    try:
        apply_memory_config(body.model_dump())
    except ValueError as exc:
        raise HTTPException(400, str(exc)) from exc
    return _memory_config_response()
