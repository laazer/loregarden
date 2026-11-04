"""The host capacity a transition's gate commands hold while they run.

Gates run lint and the organization checks at every stage transition, in every
workspace, for every agent at once; a profile may add a test suite. The lease
size is the profile's `gates.capacity_footprint`. They run in the orchestrator after the stage's agent run has
finished and its own lease is released (`_advance_after_stage`), so at that
point this thread holds nothing on the ledger. A gate lease is therefore an
ordinary top-level claim that waits in line; with nothing held, waiting cannot
deadlock.

A gate keeps its place in line for as long as the line moves
(`capacity_line`); an orderly line of test suites is a wait, not a failure
(lg-machine-resource-2112). A gate whose line stalls is not run and not passed:
it reports UNAVAILABLE, which `gate_recovery` blocks as a harness problem for a
person rather than handing to the stage's agent, because no agent can fix a
busy machine.

Amended by lg-machine-resource-1923. A gate handed its run's parent lease
(`parent_lease_id`) claims a **child** of it and never queues: the thread asking
is the thread holding the parent, so a wait here is a holder waiting on itself.
The claim is priced from the parent's unlent remainder, any excess is booked
ahead of the line, or it is refused at once. 858's reasoning still holds for a
gate with no parent — a land verification, a standalone transition — and that
branch is unchanged.
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
    DockerLease,
    DockerLeaseEndReason,
    OrchestrationRun,
    Ticket,
    Workspace,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.capacity_children import reserve_child
from loregarden.services.capacity_environment import child_environment
from loregarden.services.capacity_line import wait_while_line_moves
from loregarden.services.docker_capacity import resolve_weights
from loregarden.services.docker_leases import (
    DockerReservation,
    release_lease,
    reserve,
)
from loregarden.services.docker_ledger import OCCUPYING
from sqlmodel import Session, col, select, update

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
    parent_lease_id: str | None = None,
    orchestration_run_id: str | None = None,
) -> GateLease:
    """Hold host capacity for one transition's gate commands, or raise saying why not.

    With `parent_lease_id` the claim is a child of the run's parent lease and
    never queues; without one it is the ordinary top-level claim that waits in
    line. A parent that has gone away is **refused**, never quietly promoted to
    the top-level claim this did before 1923: that fallback would reintroduce a
    wait of minutes to hours on a path nothing reports, and the ticket would
    read as fixed.
    """
    if footprint is DockerFootprint.NONE or not settings.docker_capacity_enabled:
        return GateLease()

    label = f"gates {transition} · {workspace.slug} · {ticket.external_id or ticket.id}"
    if parent_lease_id:
        reservation = reserve_child(
            session,
            parent_lease_id=parent_lease_id,
            holder_label=label,
            footprint=footprint,
            pool=CapacityPool.HOST,
            holder_kind=DockerHolderKind.AD_HOC,
            # Stamped so the run's lease graph is readable from the run alone:
            # one parent and a child per transition, which is what the capacity
            # board draws and what the per-run assertions read.
            orchestration_run_id=orchestration_run_id,
            ticket_id=ticket.id,
            workspace_id=workspace.id,
            holder_pid=os.getpid(),
            ttl_seconds=ttl_seconds,
        )
        if reservation.granted:
            return GateLease(lease_id=reservation.lease_id, env=child_environment(reservation, {}))
        raise GateCapacityUnavailable(
            _child_refusal(session, footprint, transition, parent_lease_id, reservation)
        )

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
        stall = settings.gate_capacity_wait_seconds if wait_seconds is None else wait_seconds
        logger.warning(
            "Gates %s for %s are waiting in line for host capacity (giving up only if "
            "nothing ahead finishes for %.0fs): %s",
            transition,
            label,
            stall,
            reservation.message,
        )
        reservation = wait_while_line_moves(
            session,
            reservation,
            stall_seconds=stall,
            on_poll=(
                (lambda: _renew_run_lease(session, orchestration_run_id))
                if orchestration_run_id
                else None
            ),
        )

    if reservation.granted:
        return GateLease(lease_id=reservation.lease_id, env=child_environment(reservation, {}))
    if reservation.lease_id:
        release_lease(session, reservation.lease_id, reason=DockerLeaseEndReason.ABANDONED)
    raise GateCapacityUnavailable(
        f"Gate commands for {transition} did not run: no {footprint.value} host capacity "
        f"({reservation.error_kind or 'the line stopped moving'}) — {reservation.message}"
    )


def _renew_run_lease(session: Session, orchestration_run_id: str) -> None:
    """The orchestration waiting on this gate is alive; say so on its lease.

    The lease is renewed at stage boundaries only, and a gate waiting in line
    sits between two of them with no agent run in flight to vouch for it. Past
    `ORCHESTRATION_LEASE` the sweep would settle the run as abandoned while its
    driver was faithfully waiting — the lease-expiry half of run_f4bf51.
    """
    session.exec(
        update(OrchestrationRun)
        .where(col(OrchestrationRun.id) == orchestration_run_id)
        .values(last_seen_at=utcnow())
        .execution_options(synchronize_session=False)
    )


def _child_refusal(
    session: Session,
    footprint: DockerFootprint,
    transition: str,
    parent_lease_id: str,
    reservation: DockerReservation,
) -> str:
    """The refused child, in the same two numbers the capacity board shows.

    The ask and the parent's unlent remainder. An operator reading
    "needs 1 cpu, this run's parent holds 0.5" on the ticket and a bare
    `exceeds_parent_grant` on the board cannot tell they are one event.
    """
    cpus, _ = resolve_weights(footprint)
    parent = session.get(DockerLease, parent_lease_id)
    if parent is None or parent.status not in OCCUPYING:
        return (
            f"Gate commands for {transition} did not run: this run's parent lease "
            f"{parent_lease_id} is gone ({reservation.error_kind or 'parent_not_held'}), so "
            f"the {cpus:g}-cpu claim has nothing to draw on. A run whose standing claim has "
            "been taken does not rejoin the line mid-transition — the run is re-queued "
            f"instead. {reservation.message}"
        )
    remainder = max(0.0, parent.cpus - parent.child_covered_cpus)
    children = session.exec(
        select(DockerLease).where(
            DockerLease.parent_lease_id == parent_lease_id,
            col(DockerLease.status).in_(OCCUPYING),
        )
    ).all()
    drawing = (
        ", ".join(f"{child.holder_label} ({child.cpus:g} cpus)" for child in children) or "nothing"
    )
    return (
        f"Gate commands for {transition} did not run: needs {cpus:g} cpu; this run's parent "
        f"lease {parent_lease_id} holds {parent.cpus:g} and {remainder:g} cpus is bookable "
        f"ahead of the line ({reservation.error_kind or 'exceeds_parent_grant'}). "
        f"Drawing on it now: {drawing}. {reservation.message}"
    )
