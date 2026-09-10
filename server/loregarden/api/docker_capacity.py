"""Reading the docker capacity ledger from the dashboard.

Its own endpoint rather than another block on the queue-status payload, which
was the first design. The queue socket pushes that payload to every open tab on
a slow tick, and this read is not free: it walks the ledger to project waits, and
on a machine that has never been measured it shells out to `docker info`. Riding
the queue payload would spend that on every poll for every viewer, including the
ones who never open this tab.

The Review rail already answers the same question the same way — it fetches its
operations only while its tab is selected — so this follows a pattern the
dashboard had already settled rather than inventing a second one.

Read-only. Nothing here reserves, releases or reaps; the tab is a window on the
ledger, not a control surface for it.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from loregarden.db.session import get_session
from loregarden.services.docker_board import capacity_status
from sqlmodel import Session

router = APIRouter(prefix="/docker", tags=["docker-capacity"])


@router.get("/capacity")
def read_docker_capacity(session: Session = Depends(get_session)) -> dict:
    """The ceiling, what holds it, and who is waiting.

    `capacity_status` measures once when the ledger has never been measured, so
    the first view of this tab reports the real machine rather than the zeroes a
    never-probed pool row would show. Every later call reads the stored ceiling —
    the probe is cached, and the reconciliation sweep keeps it current.
    """
    return capacity_status(session)
