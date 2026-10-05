"""How many lanes the machine runs at once: one setting, read everywhere.

Until this module the limit was a literal 3 at every construction site — the
status reader, admission, the lane service and the slot pool each defaulted it
on their own — so changing it meant editing code and restarting. Every service
now asks here when it is not handed a number.

Sits below `parallel_queue`, so it may import only models.
"""

from __future__ import annotations

from loregarden.models.domain import QueueSettings
from loregarden.models.domain.enums import utcnow
from sqlmodel import Session

DEFAULT_LANE_COUNT = 3
#: A machine with no lanes never runs anything and looks idle doing it.
MIN_LANE_COUNT = 1
#: Each lane is a full CLI agent with its own worktree. Past this the shared
#: capacity ledger, not the lane count, is what actually bounds the machine.
MAX_LANE_COUNT = 12

SETTINGS_ID = "default"


def lane_count(session: Session) -> int:
    """The configured lane count, or the default when nobody has set one."""
    settings = session.get(QueueSettings, SETTINGS_ID)
    return settings.lane_count if settings is not None else DEFAULT_LANE_COUNT


def store_lane_count(session: Session, count: int) -> None:
    """Persist a new lane count. Validates; does not resize the pool.

    `QueueLaneService.resize` is the caller that also moves the lanes.
    """
    if not MIN_LANE_COUNT <= count <= MAX_LANE_COUNT:
        raise ValueError(f"Lane count must be between {MIN_LANE_COUNT} and {MAX_LANE_COUNT}")
    settings = session.get(QueueSettings, SETTINGS_ID) or QueueSettings(id=SETTINGS_ID)
    settings.lane_count = count
    settings.updated_at = utcnow()
    session.add(settings)
    session.commit()
