"""The living map of a Baxter chat or a ticket. See `services.living_map`."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException
from loregarden.db.session import get_session
from loregarden.models.domain import MapScope
from loregarden.services.living_map.store import LivingMapView, MapNotFound, living_map
from sqlmodel import Session

router = APIRouter(prefix="/living-map", tags=["living-map"])


@router.get("/{scope}/{ref}")
def get_living_map(
    scope: MapScope, ref: str, session: Session = Depends(get_session)
) -> LivingMapView:
    """File what the records say now and return the map in vine order.

    A plain `def`: filing reads and writes SQLite, which would block the event
    loop from an `async` handler.
    """
    try:
        return living_map(session, scope, ref)
    except MapNotFound as exc:
        raise HTTPException(404, str(exc)) from exc
