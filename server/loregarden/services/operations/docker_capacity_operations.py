"""The docker capacity board, and an operator taking a lease back, as operations.

Both were written twice, as MCP tools and as routes under `/api/docker/`.

The board is a write, though it reads like a read. `capacity_status` measures
the ceiling the first time anyone looks at a ledger that has never been
measured, which stores the probe; and an agent's poll naming its lease records
the poll and, unless throttled, runs a promotion pass. The page names no lease,
so its call measures at most once and is otherwise a read; it is declared by
what it can do, not by what it usually does.

Force release had drifted. The route required a reason and answered an unknown
lease with a 404; the tool took an empty reason and answered an unknown lease
with `ok: false`, which read like a handled case. Both now require a reason
with something in it, and an unknown lease is an error on both.
"""

from __future__ import annotations

from typing import Any

from loregarden.config import settings
from loregarden.models.domain import DockerLease, DockerWaitBasis
from loregarden.services.docker_board import capacity_status
from loregarden.services.docker_leases import drain_waiters, force_release_lease
from loregarden.services.docker_ledger import as_utc
from loregarden.services.docker_poll_guard import PollDecision, note_poll
from loregarden.services.docker_wait_estimate import UNKNOWN_WAIT, WaitEstimate, poll_interval_for
from loregarden.services.operations.spec import (
    Caller,
    Effect,
    Operation,
    OperationId,
    Transport,
)
from pydantic import BaseModel, ConfigDict, Field
from sqlmodel import Session

#: MCP callers have always been able to send keys a tool does not read.
_LENIENT = ConfigDict(extra="ignore")


class CapacityQuery(BaseModel):
    model_config = _LENIENT

    #: Marks the call as a queued caller's poll, and adds `lease` to the board.
    lease_id: str = ""
    include_waiting: bool = True


class ForceRelease(BaseModel):
    # Stripped first, so a reason of only whitespace is refused like an empty one.
    model_config = ConfigDict(extra="ignore", str_strip_whitespace=True)

    lease_id: str = Field(min_length=1)
    #: Kept on the lease, so whoever finds it ended knows why.
    reason: str = Field(min_length=1)


def _board(session: Session, query: CapacityQuery, _caller: Caller) -> dict[str, Any]:
    """The board, and the poll a queued caller uses.

    Naming a `lease_id` marks this call as a poll, which is what gets the
    back-pressure: a caller asking again inside the minimum interval is served
    from the row it already has and told when to come back, without a promotion
    pass. Skipping that delays nothing — the reconciliation timer runs it
    regardless — and it is the difference between a retry loop costing a read
    and one costing a write per iteration.
    """
    lease_id = query.lease_id
    lease = session.get(DockerLease, lease_id) if lease_id else None

    decision: PollDecision | None = None
    if lease is not None:
        decision = note_poll(session, lease)
        if not decision.throttled:
            # A served poll advances the queue for a caller that has no timer of
            # its own — additive to the sweep, never a substitute for it.
            drain_waiters(session)
            session.refresh(lease)

    board = capacity_status(session)
    estimate = _waiting_estimate(board, lease_id) if lease_id else UNKNOWN_WAIT
    if not query.include_waiting:
        board.pop("waiting", None)

    if lease_id:
        board["lease"] = (
            {"lease_id": lease_id, "found": False}
            if lease is None
            else {
                "lease_id": lease.id,
                "found": True,
                "status": lease.status.value,
                "position": lease.position or None,
                **estimate.as_dict(),
                "poll_after_seconds": poll_interval_for(estimate),
                "expires_at": (as_utc(lease.expires_at).isoformat() if lease.expires_at else None),
            }
        )
    if decision is not None:
        board["poll"] = decision.as_dict()
        if decision.throttled:
            board["poll"]["message"] = (
                "Polled again after less than "
                f"{int(settings.docker_poll_min_interval_seconds)}s. The state below is "
                f"current; wait {decision.retry_after_seconds}s before asking again."
            )
    return board


def _waiting_estimate(board: dict, lease_id: str) -> WaitEstimate:
    """This lease's projected wait, read off the board rather than recomputed.

    Recomputing would walk the queue a second time and could disagree with the
    `waiting` list in the same payload — two numbers for one question is worse
    than one imperfect number.
    """
    for entry in board.get("waiting", []):
        if entry.get("lease_id") == lease_id:
            return WaitEstimate(
                seconds=entry.get("estimated_wait_seconds"),
                basis=DockerWaitBasis(entry.get("estimate_basis", DockerWaitBasis.UNKNOWN.value)),
            )
    return UNKNOWN_WAIT


def _force_release(session: Session, request: ForceRelease, _caller: Caller) -> dict[str, Any]:
    """End a lease: release a holder's grant, or drop a waiter from the line.

    `released` is false when the lease had already ended — a second click, or
    the sweep getting there first — which is not a failure: the lease is gone
    either way. An id nothing answers to raises `UnknownLease`.
    """
    released = force_release_lease(session, request.lease_id, reason=request.reason)
    return {"lease_id": request.lease_id, "released": released, "reason": request.reason}


def _agent_release_reply(session: Session, result: dict[str, Any]) -> dict[str, Any]:
    """An agent is told what force release did not do, and what is free now."""
    return {
        "ok": True,
        **result,
        "warning": (
            "The ledger no longer accounts for this lease. Any containers it "
            "started are still running — stop them yourself if they are stale."
        ),
        "available": capacity_status(session)["available"],
    }


def _page_release_reply(_session: Session, result: dict[str, Any]) -> dict[str, Any]:
    return {"lease_id": result["lease_id"], "released": result["released"]}


DOCKER_CAPACITY_OPERATIONS = (
    Operation(
        id=OperationId.DOCKER_CAPACITY_STATUS,
        effect=Effect.WRITE,
        args=CapacityQuery,
        handler=_board,
    ),
    Operation(
        id=OperationId.DOCKER_LEASE_FORCE_RELEASE,
        effect=Effect.WRITE,
        args=ForceRelease,
        handler=_force_release,
        replies={
            Transport.MCP: _agent_release_reply,
            Transport.CLI: _agent_release_reply,
            Transport.REST: _page_release_reply,
        },
    ),
)
