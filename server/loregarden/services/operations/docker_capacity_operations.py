"""The docker capacity board, and an operator taking a lease back, as operations.

Both were written twice, as MCP tools and as routes under `/api/docker/`.

An agent's board is a write, though it reads like a read: its poll naming a
lease records the poll and, unless throttled, runs a promotion pass, and it
measures a ceiling nobody has measured yet. The page's board is a separate
operation and a read: it names no lease and measures nothing, since the reap
sweep measures a never-measured pool at startup (`docker_reaper`).

Prioritizing a waiter is the third: the same operation behind the MCP tool (and
so the CLI's `mcp call`) and the REST route, so both require the same reason.

Force release had drifted. The route required a reason and answered an unknown
lease with a 404; the tool took an empty reason and answered an unknown lease
with `ok: false`, which read like a handled case. Both now require a reason
with something in it, and an unknown lease is an error on both.
"""

from __future__ import annotations

from typing import Any

from loregarden.config import settings
from loregarden.models.domain import DockerLease, DockerWaitBasis
from loregarden.services.capacity_priority import prioritize_lease
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
_LENIENT = ConfigDict(extra="ignore", use_attribute_docstrings=True)


class CapacityQuery(BaseModel):
    model_config = _LENIENT

    #: Marks the call as a queued caller's poll, and adds `lease` to the board.
    lease_id: str = ""
    """Optionally report on just this lease, as `lease`, alongside the board."""
    include_waiting: bool = True
    """Include the queue. Defaults to true."""


class BoardQuery(BaseModel):
    model_config = _LENIENT

    include_waiting: bool = True
    """Include the queue. Defaults to true."""


class ForceRelease(BaseModel):
    # Stripped first, so a reason of only whitespace is refused like an empty one.
    model_config = ConfigDict(
        extra="ignore", use_attribute_docstrings=True, str_strip_whitespace=True
    )

    lease_id: str = Field(min_length=1)
    """The lease to take back."""
    #: Kept on the lease, so whoever finds it ended knows why.
    reason: str = Field(min_length=1)
    """Why this lease is being taken from its holder."""


class Prioritize(BaseModel):
    # Stripped first, so a reason of only whitespace is refused like an empty one.
    model_config = ConfigDict(
        extra="ignore", use_attribute_docstrings=True, str_strip_whitespace=True
    )

    lease_id: str = Field(min_length=1)
    """The waiting lease to put ahead of the line."""
    #: Kept on the lease and shown on the board, so a jump is attributable.
    reason: str = Field(min_length=1)
    """Why this lease should go ahead of every normal waiter."""


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
                low_seconds=entry.get("estimated_wait_low_seconds"),
                high_seconds=entry.get("estimated_wait_high_seconds"),
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


def _prioritize(session: Session, request: Prioritize, _caller: Caller) -> dict[str, Any]:
    """Move a waiting lease into the priority tier.

    `moved` is false when it was already there, which is not a failure: it kept
    its place. An unknown id raises `UnknownLease`; a lease that holds capacity
    or has ended raises `LeaseNotWaiting`.
    """
    return prioritize_lease(session, request.lease_id, reason=request.reason).as_dict()


def _page_board(session: Session, query: BoardQuery, _caller: Caller) -> dict[str, Any]:
    """The board, read: no poll is recorded and no ceiling measured."""
    board = capacity_status(session, measure_if_unknown=False)
    if not query.include_waiting:
        board.pop("waiting", None)
    return board


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
        unavailable_on={
            Transport.REST: "the page reads docker_capacity_board, which records no poll",
        },
    ),
    Operation(
        id=OperationId.DOCKER_CAPACITY_BOARD,
        effect=Effect.READ,
        args=BoardQuery,
        handler=_page_board,
        unavailable_on={
            Transport.MCP: "agents read docker_capacity_status, which also takes their poll",
            Transport.CLI: "the CLI runs MCP's tools, which do not offer it",
        },
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
    Operation(
        id=OperationId.DOCKER_LEASE_PRIORITIZE,
        effect=Effect.WRITE,
        args=Prioritize,
        handler=_prioritize,
    ),
)
