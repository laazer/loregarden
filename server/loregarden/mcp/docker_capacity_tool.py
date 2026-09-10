"""The docker capacity MCP tools: schemas and handlers.

Its own module, registered through `EXTENDED_TOOLS` — `mcp/tools.py` is at its
size cap and `execute_tool` past its complexity cap, so a new branch there is
not available and would not be the right shape anyway.

**Nothing here blocks for long.** `reserve` returns immediately with either a
lease or a place in line, and tells the caller when to poll. An MCP call runs
inside the agent's turn and spends the run's wall-clock budget, and the CLI
mirror runs in-process against SQLite where a blocking call holds a whole-file
lock. `wait_seconds` exists for the common case — the pool frees in a moment and
one round trip beats three — and is clamped hard for exactly those reasons.

Every payload is self-classifying: a refusal comes back with an `error_kind`
rather than as an exception, because an agent that cannot tell "wait your turn"
from "this will never fit" will do the wrong one of the two.
"""

from __future__ import annotations

import json
import time
from datetime import datetime, timezone
from typing import Any

from sqlmodel import Session

from loregarden.config import settings
from loregarden.mcp.tool_ids import McpTool
from loregarden.mcp.tool_schemas import (
    boolean_prop,
    enum_string_prop,
    integer_prop,
    string_list_prop,
    string_prop,
    tool_schema,
)
from loregarden.models.domain import (
    DockerFootprint,
    DockerGrantState,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
)
from loregarden.services.docker_board import capacity_status
from loregarden.services.docker_capacity import CLASS_WEIGHTS, ClaimTooVague
from loregarden.services.docker_leases import (
    REJECT_LEASE_NOT_HELD,
    REJECT_UNKNOWN_LEASE,
    as_utc,
    drain_waiters,
    release_lease,
    renew_lease,
    reserve,
)

#: How long a queued caller should wait before asking again. Short enough that a
#: freed lease is picked up promptly, long enough that polling is not a spin.
POLL_AFTER_SECONDS = 15

_FOOTPRINT_VALUES = [f.value for f in CLASS_WEIGHTS]

_RESERVE_DEFINITION: dict[str, Any] = {
    "name": McpTool.RESERVE_DOCKER_CAPACITY,
    "description": (
        "Reserve capacity on this machine's Docker daemon BEFORE starting "
        "containers, so parallel runs do not over-subscribe it. Returns "
        "immediately: `state` is `granted` (start your containers), `queued` "
        "(wait — poll loregarden_docker_capacity_status after "
        "`poll_after_seconds`; your place in line is kept) or `rejected` (read "
        "`error_kind`; the claim will never fit, or Docker cannot be reached). "
        "Being queued is not a failure. Do NOT start containers while queued. "
        "Once granted, call loregarden_renew_docker_lease with the project or "
        "container names you started — that is what lets the lease be reclaimed "
        "by checking Docker rather than by guessing — and renew before "
        "`expires_at`. Release with loregarden_release_docker_capacity when done."
    ),
    "inputSchema": tool_schema(
        properties={
            "holder_label": string_prop(
                "Who is asking and what for, e.g. 'blobert e2e suite'. Shown on "
                "the board; an unlabelled lease tells an operator nothing about "
                "what to go and stop."
            ),
            "footprint": enum_string_prop(
                "How big the claim is. "
                + ", ".join(
                    f"{name}={weights[0]} cpus/{weights[1]}MB"
                    for name, weights in ((f.value, CLASS_WEIGHTS[f]) for f in CLASS_WEIGHTS)
                )
                + ". Pass this OR both cpus and memory_mb.",
                _FOOTPRINT_VALUES,
            ),
            "cpus": {
                "type": "number",
                "description": "Explicit cpu cost. Requires memory_mb as well.",
            },
            "memory_mb": integer_prop("Explicit memory cost in MB. Requires cpus as well."),
            "ttl_seconds": integer_prop(
                "How long the lease is good for without renewal. Defaults to "
                f"{settings.docker_lease_ttl_seconds}s."
            ),
            "wait_seconds": integer_prop(
                "Optionally wait inline this long for a queued claim to be "
                f"granted. Clamped to {int(settings.docker_lease_inline_wait_max_seconds)}s "
                "— longer waits belong in a poll, not in your turn."
            ),
            "ticket_id": string_prop("Ticket this work belongs to, if any."),
            "holder_pid": integer_prop(
                "A shell pid to tie the lease to. A pid that no longer exists "
                "settles liveness outright, with no Docker call."
            ),
        },
        required=["holder_label"],
    ),
}

_RENEW_DEFINITION: dict[str, Any] = {
    "name": McpTool.RENEW_DOCKER_LEASE,
    "description": (
        "Renew a held docker lease, and record what it actually started. Call "
        "this right after your containers come up, passing `compose_project` "
        "and/or `container_names`: a lease that names nothing can only be "
        "reclaimed on its clock, while one that names its containers is checked "
        "against Docker before anything is taken back. Then call it again "
        "before `expires_at` for as long as you need the capacity."
    ),
    "inputSchema": tool_schema(
        properties={
            "lease_id": string_prop("The lease to renew."),
            "compose_project": string_prop(
                "Compose project name your containers carry, if you started a stack."
            ),
            "container_names": string_list_prop("Container names you started, if any."),
            "ttl_seconds": integer_prop("New TTL. Omit to keep the current one."),
        },
        required=["lease_id"],
    ),
}

_RELEASE_DEFINITION: dict[str, Any] = {
    "name": McpTool.RELEASE_DOCKER_CAPACITY,
    "description": (
        "Hand a docker lease back once your containers are down. Idempotent. "
        "Releasing promptly is what keeps the ledger accurate — a lease left to "
        "expire holds capacity nobody is using until the reaper notices."
    ),
    "inputSchema": tool_schema(
        properties={
            "lease_id": string_prop("The lease to release."),
            "note": string_prop("Optional note recorded against the lease."),
        },
        required=["lease_id"],
    ),
}

_STATUS_DEFINITION: dict[str, Any] = {
    "name": McpTool.DOCKER_CAPACITY_STATUS,
    "description": (
        "The docker capacity board: the ceiling and where it came from, what is "
        "held, who is waiting and in what order, and any lease the reaper could "
        "not verify. Read-only. This is also the poll for a queued reservation — "
        "find your lease_id under `holders` and it has been granted. Check "
        "`ceiling.source`: `stale_probe` means the number is the last good "
        "measurement and Docker is currently unreachable."
    ),
    "inputSchema": tool_schema(
        properties={
            "lease_id": string_prop(
                "Optionally report on just this lease, as `lease`, alongside the board."
            ),
            "include_waiting": boolean_prop("Include the queue. Defaults to true."),
        },
        required=[],
    ),
}

_FORCE_RELEASE_DEFINITION: dict[str, Any] = {
    "name": McpTool.FORCE_RELEASE_DOCKER_LEASE,
    "description": (
        "Take a docker lease back from its holder. This does NOT stop any "
        "containers — it only stops the ledger accounting for them, so the "
        "capacity can be double-booked if the holder is genuinely still running. "
        "For an operator clearing a lease whose owner is known to be gone. "
        "Requires a reason."
    ),
    "inputSchema": tool_schema(
        properties={
            "lease_id": string_prop("The lease to take back."),
            "reason": string_prop("Why this lease is being taken from its holder."),
        },
        required=["lease_id", "reason"],
    ),
}

TOOL_DEFINITIONS: list[dict[str, Any]] = [
    _RESERVE_DEFINITION,
    _RENEW_DEFINITION,
    _RELEASE_DEFINITION,
    _STATUS_DEFINITION,
    _FORCE_RELEASE_DEFINITION,
]


def _dump(payload: dict) -> str:
    return json.dumps(payload, indent=2, default=str)


def reserve_docker_capacity_tool(session: Session, arguments: dict[str, Any]) -> str:
    footprint_name = arguments.get("footprint") or ""
    footprint = DockerFootprint(footprint_name) if footprint_name else DockerFootprint.CUSTOM
    try:
        reservation = reserve(
            session,
            holder_label=arguments["holder_label"],
            footprint=footprint,
            cpus=float(arguments.get("cpus") or 0.0),
            memory_mb=int(arguments.get("memory_mb") or 0),
            ttl_seconds=arguments.get("ttl_seconds") or None,
            ticket_id=arguments.get("ticket_id") or None,
            holder_pid=arguments.get("holder_pid") or None,
        )
    except ClaimTooVague as exc:
        # Returned, not raised: the agent needs to know this is its own argument
        # error and not a full machine, and those call for opposite responses.
        return _dump(
            {
                "state": DockerGrantState.REJECTED.value,
                "error_kind": "claim_too_vague",
                "message": str(exc),
                "footprints": {
                    name.value: {"cpus": weights[0], "memory_mb": weights[1]}
                    for name, weights in CLASS_WEIGHTS.items()
                },
            }
        )

    if reservation.state is DockerGrantState.QUEUED:
        reservation = _wait_briefly(session, reservation, arguments.get("wait_seconds") or 0)

    payload = reservation.as_dict()
    payload["poll_after_seconds"] = (
        POLL_AFTER_SECONDS if reservation.state is DockerGrantState.QUEUED else None
    )
    if reservation.granted and reservation.expires_at is not None:
        payload["renew_after_seconds"] = max(
            1, int((reservation.expires_at - datetime.now(timezone.utc)).total_seconds() // 2)
        )
    payload["capacity"] = capacity_status(session)["available"]
    return _dump(payload)


def clamp_inline_wait(wait_seconds: float) -> float:
    """How long `reserve` may actually sit and wait, whatever the caller asked.

    A named function rather than an inline `min` so the cap can be asserted
    without timing anything. Bounding a *measured* elapsed time by a literal is
    the shape `test_suite_clock_hermeticity` exists to reject: it passes on an
    idle machine, fails under load, and additionally claims the handler is fast —
    which no criterion here says. What this feature actually promises is that the
    request is clamped, and that is a pure function of its argument.

    The cap is not a tuning preference. This runs inside the agent's turn, so an
    unbounded wait spends the run's own timeout budget on sitting still.
    """
    return max(0.0, min(float(wait_seconds), settings.docker_lease_inline_wait_max_seconds))


def _wait_briefly(session: Session, reservation, wait_seconds: int):
    """Poll our own lease for a moment before telling the caller to come back."""
    budget = clamp_inline_wait(wait_seconds)
    if budget <= 0:
        return reservation

    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        time.sleep(min(0.5, max(0.05, budget / 10)))
        drain_waiters(session)
        session.expire_all()
        lease = session.get(DockerLease, reservation.lease_id)
        if lease is not None and lease.status is DockerLeaseStatus.HELD:
            reservation.state = DockerGrantState.GRANTED
            reservation.position = None
            reservation.ahead = 0
            reservation.expires_at = as_utc(lease.expires_at)
            reservation.message = f"Granted {lease.cpus} cpus / {lease.memory_mb} MB."
            return reservation
    return reservation


def renew_docker_lease_tool(session: Session, arguments: dict[str, Any]) -> str:
    lease_id = arguments["lease_id"]
    project = arguments.get("compose_project") or ""
    names = list(arguments.get("container_names") or [])

    lease = session.get(DockerLease, lease_id)
    if lease is None:
        return _dump({"ok": False, "error_kind": REJECT_UNKNOWN_LEASE, "lease_id": lease_id})

    if project or names:
        if project:
            lease.compose_project = project
        if names:
            lease.container_names_json = json.dumps(names)
        session.add(lease)
        session.commit()

    expires_at = renew_lease(session, lease_id, ttl_seconds=arguments.get("ttl_seconds") or None)
    if expires_at is None:
        session.refresh(lease)
        return _dump(
            {
                "ok": False,
                "error_kind": REJECT_LEASE_NOT_HELD,
                "lease_id": lease_id,
                "status": lease.status.value,
                "message": (
                    "This lease is no longer held, so there was nothing to renew. "
                    "Reserve again before starting containers."
                ),
            }
        )
    return _dump(
        {
            "ok": True,
            "lease_id": lease_id,
            "expires_at": expires_at.isoformat(),
            "renew_after_seconds": max(1, lease.ttl_seconds // 2),
            "compose_project": lease.compose_project,
            "container_names": names or json.loads(lease.container_names_json or "[]"),
        }
    )


def release_docker_capacity_tool(session: Session, arguments: dict[str, Any]) -> str:
    lease_id = arguments["lease_id"]
    note = arguments.get("note") or ""
    if note:
        lease = session.get(DockerLease, lease_id)
        if lease is not None:
            lease.note = note
            session.add(lease)
            session.commit()

    released = release_lease(session, lease_id, reason=DockerLeaseEndReason.RELEASED)
    board = capacity_status(session)
    return _dump(
        {
            "ok": True,
            "released": released,
            "lease_id": lease_id,
            "message": ("Released." if released else "Already settled; nothing to release."),
            "available": board["available"],
            "waiting": len(board["waiting"]),
        }
    )


def docker_capacity_status_tool(session: Session, arguments: dict[str, Any]) -> str:
    board = capacity_status(session)
    if not arguments.get("include_waiting", True):
        board.pop("waiting", None)

    lease_id = arguments.get("lease_id") or ""
    if lease_id:
        lease = session.get(DockerLease, lease_id)
        board["lease"] = (
            {"lease_id": lease_id, "found": False}
            if lease is None
            else {
                "lease_id": lease.id,
                "found": True,
                "status": lease.status.value,
                "position": lease.position or None,
                "expires_at": (as_utc(lease.expires_at).isoformat() if lease.expires_at else None),
            }
        )
    return _dump(board)


def force_release_docker_lease_tool(session: Session, arguments: dict[str, Any]) -> str:
    lease_id = arguments["lease_id"]
    lease = session.get(DockerLease, lease_id)
    if lease is None:
        return _dump({"ok": False, "error_kind": REJECT_UNKNOWN_LEASE, "lease_id": lease_id})

    lease.note = f"force-released: {arguments['reason']}"
    session.add(lease)
    session.commit()
    released = release_lease(session, lease_id, reason=DockerLeaseEndReason.FORCE_RELEASED)
    return _dump(
        {
            "ok": True,
            "released": released,
            "lease_id": lease_id,
            "reason": arguments["reason"],
            "warning": (
                "The ledger no longer accounts for this lease. Any containers it "
                "started are still running — stop them yourself if they are stale."
            ),
            "available": capacity_status(session)["available"],
        }
    )
