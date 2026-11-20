"""`loregarden_claim_paths`: a run declares the files it is about to change.

A plan names files before any run starts, and a run's changed paths are known
only once it ends. In between, an agent that is about to change a signature or
move a module can say so now, and every open sibling in its landing tree sees
the claim in its next stage prompt (`agents.sibling_claims_context`).

The reply names the siblings already claiming those files, so the agent learns
of a collision while it can still choose a smaller change.
"""

from __future__ import annotations

from typing import Any

from loregarden.models.domain import AgentRun
from loregarden.services.operations.run_callbacks import RunCall
from loregarden.services.operations.spec import Caller, Effect, Operation, OperationId, Transport
from loregarden.services.path_claims import declare_paths, paths_overlap, sibling_overlaps
from loregarden.services.run_resolution import attributed_agent_run_id
from pydantic import Field
from sqlmodel import Session


class PathsClaim(RunCall):
    paths: list[str] = Field(min_length=1)
    """Repo-relative paths you are about to change; a trailing / claims a directory."""
    reason: str = Field(min_length=10)
    """What you are about to do there, in a sentence, for the sibling that reads it."""


def _claim_paths(session: Session, call: PathsClaim, caller: Caller) -> dict[str, Any]:
    ref, ticket = call.target(session)
    # Limited to the caller's own ticket: a supervised run naming another
    # ticket's run would put claims on work it is not doing.
    if caller.run_id:
        supervised = session.get(AgentRun, caller.run_id)
        if supervised is not None and supervised.ticket_id != ticket.id:
            raise ValueError(
                f"run {call.run_id} belongs to {ticket.external_id}, not to the ticket this "
                "run is working; claim paths only for your own ticket"
            )
    claimed = declare_paths(
        session,
        ticket,
        call.paths,
        reason=call.reason,
        run_id=attributed_agent_run_id(session, ref, transport_run_id=caller.run_id),
    )
    collisions = [
        {
            "ticket": overlap.sibling.external_id,
            "paths": shared,
        }
        for overlap in sibling_overlaps(session, ticket)
        if (
            shared := [
                c.path for c in overlap.shared if any(paths_overlap(c.path, p) for p in call.paths)
            ]
        )
    ]
    return {"ok": True, "ticket": ticket.external_id, "claimed": claimed, "siblings": collisions}


PATH_CLAIM_OPERATIONS = (
    Operation(
        id=OperationId.PATHS_CLAIM,
        effect=Effect.WRITE,
        args=PathsClaim,
        handler=_claim_paths,
        unavailable_on={
            Transport.REST: "a declaration an agent makes mid-run; no page makes one",
        },
    ),
)
