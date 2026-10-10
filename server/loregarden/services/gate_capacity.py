"""The host capacity a transition's gate commands hold while they run.

Gates run lint and the organization checks at every stage transition, in every
workspace, for every agent at once; a profile may add a test suite. The lease
size is the profile's `gates.capacity_footprint`. They run in the orchestrator after the stage's agent run has
finished and its own lease is released (`_advance_after_stage`), so at that
point this thread holds nothing on the ledger. A gate lease is therefore an
ordinary top-level claim that waits in line; with nothing held, waiting cannot
deadlock.

A gate that cannot get capacity in time is not run and not passed: it reports
UNAVAILABLE, which `gate_recovery` routes to a human rather than to the
stage's agent, because no agent can fix a busy machine.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field

from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLeaseEndReason,
    Ticket,
    Workspace,
)
from loregarden.services.capacity_run import child_environment
from loregarden.services.docker_leases import release_lease, reserve, wait_for_grant
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass
class GateLease:
    """A held gate lease and the environment its commands get, or nothing held."""

    lease_id: str = ""
    env: dict[str, str] = field(default_factory=dict)

    def release(self, session: Session) -> None:
        if self.lease_id:
            release_lease(session, self.lease_id, reason=DockerLeaseEndReason.RELEASED)


class GateCapacityUnavailable(RuntimeError):
    """The gate's lease could not be had within its budget. Carries the reason."""


def acquire_gate_capacity(
    session: Session,
    footprint: DockerFootprint,
    *,
    ticket: Ticket,
    workspace: Workspace,
    transition: str,
    ttl_seconds: int,
    wait_seconds: float | None = None,
) -> GateLease:
    """Hold host capacity for one transition's gate commands, or raise saying why not."""
    if footprint is DockerFootprint.NONE or not settings.docker_capacity_enabled:
        return GateLease()

    label = f"gates {transition} · {workspace.slug} · {ticket.external_id or ticket.id}"
    reservation = reserve(
        session,
        holder_label=label,
        footprint=footprint,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.AD_HOC,
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        # The orchestrating process: if it dies mid-gate the reaper frees this
        # at once rather than at TTL.
        holder_pid=os.getpid(),
        ttl_seconds=ttl_seconds,
    )
    if reservation.state is DockerGrantState.QUEUED:
        budget = settings.gate_capacity_wait_seconds if wait_seconds is None else wait_seconds
        logger.warning(
            "Gates %s for %s are waiting up to %.0fs for host capacity: %s",
            transition,
            label,
            budget,
            reservation.message,
        )
        reservation = wait_for_grant(session, reservation, budget=budget)

    if reservation.granted:
        return GateLease(lease_id=reservation.lease_id, env=child_environment(reservation, {}))
    if reservation.lease_id:
        release_lease(session, reservation.lease_id, reason=DockerLeaseEndReason.ABANDONED)
    raise GateCapacityUnavailable(
        f"Gate commands for {transition} did not run: no {footprint.value} host capacity "
        f"({reservation.error_kind or 'still queued'}) — {reservation.message}"
    )
