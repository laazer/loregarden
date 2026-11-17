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

Reads, plus one write: an operator ending a lease from the board. Nothing here
reserves or reaps — that stays with the callers and the sweep. Ending a lease
is the same `force_release_lease` the MCP tool calls, so the board and an agent
cannot leave the ledger in two different states for one decision.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends
from loregarden.api.operation_bodies import OperationBody
from loregarden.api.operation_routes import run_rest_operation
from loregarden.db.session import get_session
from loregarden.services.operations import OperationId
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
    return run_rest_operation(session, OperationId.DOCKER_CAPACITY_STATUS, {})


class ForceReleaseRequest(
    OperationBody, operation=OperationId.DOCKER_LEASE_FORCE_RELEASE, supplied={"lease_id"}
):
    """Why the lease is ended; kept on it."""


@router.post("/capacity/leases/{lease_id}/release")
def force_release_docker_lease(
    lease_id: str,
    body: ForceReleaseRequest,
    session: Session = Depends(get_session),
) -> dict:
    """End a lease: release a holder's grant, or drop a waiter from the line.

    `released` is false when the lease had already ended — a second click, or
    the sweep getting there first — which is not a failure: the lease is gone
    either way. An id nothing answers to is a 404.
    """
    return run_rest_operation(
        session,
        OperationId.DOCKER_LEASE_FORCE_RELEASE,
        {"lease_id": lease_id, "reason": body.reason},
    )
