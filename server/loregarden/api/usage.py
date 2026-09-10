from fastapi import APIRouter
from loregarden.services.usage_snapshot_cache import read_usage_snapshot

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
def read_usage(refresh: bool = False) -> dict:
    """The usage snapshot. ``refresh=true`` waits for live provider numbers."""
    return read_usage_snapshot(force=refresh)
