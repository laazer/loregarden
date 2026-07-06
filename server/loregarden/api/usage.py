from fastapi import APIRouter
from loregarden.services.usage_service import get_usage_snapshot

router = APIRouter(prefix="/usage", tags=["usage"])


@router.get("")
def read_usage() -> dict:
    return get_usage_snapshot()
