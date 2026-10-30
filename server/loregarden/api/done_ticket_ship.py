"""Which done tickets have a ship that is not green — Home's and Work items' read.

Reads the recorded `ShipProbe` and never asks GitHub. The probe is written on
the reconcile timer (`services.done_ticket_ship`), so a surface polling every
15 seconds costs one row read, and a workspace GitHub could not be asked about
comes back as `listing_state: unavailable` with its last-known violations
rather than as an empty, green-looking list.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Query
from loregarden.db.session import get_session
from loregarden.models.domain import ShipProbeView
from loregarden.services.done_ticket_ship import load_probes
from pydantic import BaseModel
from sqlmodel import Session

router = APIRouter(tags=["ships"])


class DoneTicketShipResponse(BaseModel):
    probes: list[ShipProbeView]


@router.get("/done-ticket-ship", response_model=DoneTicketShipResponse)
def get_done_ticket_ship(
    workspace: str = Query("", description="Only this workspace, by slug."),
    session: Session = Depends(get_session),
) -> DoneTicketShipResponse:
    return DoneTicketShipResponse(probes=load_probes(session, workspace=workspace))
