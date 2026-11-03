"""One host capacity lease per orchestration run, and a child of it per gate.

Every transition gate used to take its own top-level host lease and give it
back when the transition ended, so a ticket paid the FIFO line once per gate.
Measured on `lg-reference-sources-1912` (2026-11-02): of 57 minutes wall-clock,
8 minutes was agent work and **29 minutes was waiting in the capacity queue** —
6m24s at `triage_to_plan` and 22m40s at `plan_to_plan-synthesis`. Neither wait
was scarcity. At 15:46 the pool had 2.75 cpu free and the gate wanted 1.0, but a
4.0-cpu `land-verify` waiter sat at the head of the line and `drain_waiters`
stops at the first head that does not fit, which is what head-of-line means.

So a run takes **one** standing claim and each gate draws a **child** of it.
`capacity_children.reserve_child` is the mechanism, unchanged: a child is priced
from the parent's unlent remainder, any excess is booked ahead of the queue, or
it is refused at once. A child never waits, so a holder never waits.

**The parent is keyed on the orchestration run, not the ticket.** A park a
person owes must give the capacity back — a cpu pinned overnight on a 7-cpu
machine by a ticket sitting in somebody's inbox, with no dead pid for the reaper
to find, is the one shape the operator's decision rules out. `_pause_orchestration`
completes the run SUCCEEDED, so the resume is a *new* run with a new parent and a
second, visible trip to the line. A ticket-keyed parent surviving the park would
make that unimplementable.

**Which parks hold it is a per-park question**, answered by
`autopilot_sign_off.signer_for_gate` and never by `orchestration_runs.auto_approve`:
an `auto_approve` run still parks for a person on an authored question, an
authority grant, a recheck, a `mark_needs_person` and a block, and keying the
hold on the flag would hold the lease through every one of them. The predicate
decides; `BuiltinOrchestrator.execute()`'s one `finally` guarantees — a retry
budget block, a stage limit, a dependency hold and an exception at the run
boundary are exits no predicate enumerates.
"""

from __future__ import annotations

import logging
import os
import threading
from collections import defaultdict

from loregarden.config import settings
from loregarden.models.domain import (
    Approval,
    CapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
    Workspace,
)
from loregarden.services.autopilot_sign_off import self_and_ancestors, signer_for_gate
from loregarden.services.capacity_children import reserve_child
from loregarden.services.docker_capacity import resolve_weights
from loregarden.services.docker_leases import (
    DockerReservation,
    release_lease,
    reserve,
    wait_for_grant,
)
from loregarden.services.docker_ledger import OCCUPYING, as_utc
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: One claim per run, serialized in this process. The dedupe inside `reserve` is
#: a read followed by an insert in a later transaction with nothing behind it, so
#: two claimants for one run — a server thread and the reaper, an `execute()`
#: recursing into a child whose own driver is already running — can both read
#: "nothing held" and both insert. Two standing claims for one run is 0.5 cpu
#: that will be leaked with a live `holder_pid`, which no reaper ever takes.
#: Cross-process claimants are caught after the fact by `_settle_duplicates`.
_CLAIM_LOCKS: defaultdict[str, threading.Lock] = defaultdict(threading.Lock)
_CLAIM_LOCKS_GUARD = threading.Lock()


def _claim_lock(orchestration_run_id: str) -> threading.Lock:
    with _CLAIM_LOCKS_GUARD:
        return _CLAIM_LOCKS[orchestration_run_id]


def parent_lease_for(session: Session, orch_run: OrchestrationRun) -> DockerLease | None:
    """This run's live parent host lease, or None.

    Deliberately not filtered on `parent_lease_id IS NULL`: a nested
    child-ticket run's parent is itself a child of the outer run's, and a lookup
    that skipped it would book a second standing claim on every transition of
    the inner run.

    Filtered on `holder_kind` and `agent_run_id IS NULL` because `run_capacity`
    stamps every agent-run host lease with the orchestration's id as well as its
    own; a 0.25-cpu AGENT_RUN lease handed out as this run's parent is a grant
    that disappears under its gate children mid-gate.
    """
    return session.exec(
        select(DockerLease)
        .where(
            DockerLease.orchestration_run_id == orch_run.id,
            DockerLease.pool == CapacityPool.HOST,
            DockerLease.holder_kind == DockerHolderKind.ORCHESTRATION,
            col(DockerLease.agent_run_id).is_(None),
            col(DockerLease.status).in_(OCCUPYING),
        )
        .order_by(col(DockerLease.position), col(DockerLease.id))
    ).first()


def enclosing_parent_lease(session: Session, ticket: Ticket) -> DockerLease | None:
    """The standing claim of a run enclosing this one, or None.

    `BuiltinOrchestrator.execute()` orchestrates incomplete child tickets inside
    its own try block, so the outer run is holding its parent while the inner
    run starts. An inner run that took a second *top-level* parent would be a
    holder that waits — the shape `capacity_children` exists to forbid — and a
    subtree would pay the FIFO line once per ticket in it.

    Derived from the ticket's ancestry and the ledger rather than threaded
    through the gate layers, so there is no second copy of the lease id to drift
    against. `holder_pid` pins it to this process: another machine-process's
    standing claim is not ours to draw on.
    """
    ancestors = self_and_ancestors(session, ticket)[1:]
    if not ancestors:
        return None
    return session.exec(
        select(DockerLease)
        .where(
            col(DockerLease.ticket_id).in_(ancestors),
            DockerLease.pool == CapacityPool.HOST,
            DockerLease.holder_kind == DockerHolderKind.ORCHESTRATION,
            DockerLease.holder_pid == os.getpid(),
            col(DockerLease.agent_run_id).is_(None),
            col(DockerLease.status).in_(OCCUPYING),
        )
        .order_by(col(DockerLease.position), col(DockerLease.id))
    ).first()


def parent_label(orch_run: OrchestrationRun, ticket: Ticket, workspace: Workspace) -> str:
    """What the capacity board calls this claim: the run, the repo, the ticket."""
    return f"run {orch_run.run_code} · {workspace.slug} · {ticket.external_id or ticket.id}"


def acquire_or_find_parent(
    session: Session,
    *,
    orch_run: OrchestrationRun,
    ticket: Ticket,
    workspace: Workspace,
    footprint: DockerFootprint,
    outer_parent_lease_id: str | None = None,
    wait_seconds: float | None = None,
) -> DockerReservation:
    """The one standing host claim for this run — found, or taken now.

    Acquired lazily, on the first transition gate of the run, so stages before
    the first gate pay no standing hold and a failure to acquire has a
    `GateRunResult` to report through.

    `outer_parent_lease_id` is the enclosing run's parent when `execute()` has
    recursed into a child ticket: the inner claim is then a *child* of it, so a
    subtree pays the line once and the inner run never waits for room only the
    outer run's completion would free.

    Returns a `DockerReservation` because that is the vocabulary every other
    claim in this ledger speaks, and `reused=True` on a second call is already
    what `reserve` means by "this run already holds one".
    """
    with _claim_lock(orch_run.id):
        existing = parent_lease_for(session, orch_run)
        if existing is not None:
            return _reused(session, existing)

        cpus, memory_mb = resolve_weights(footprint)
        label = parent_label(orch_run, ticket, workspace)
        if outer_parent_lease_id:
            reservation = reserve_child(
                session,
                parent_lease_id=outer_parent_lease_id,
                holder_label=label,
                footprint=footprint,
                pool=CapacityPool.HOST,
                holder_kind=DockerHolderKind.ORCHESTRATION,
                orchestration_run_id=orch_run.id,
                ticket_id=ticket.id,
                workspace_id=workspace.id,
                holder_pid=os.getpid(),
                # Nested for liveness and for the cascade, not to be paid for
                # out of the outer run's allotment: that allotment exists to
                # cover the outer run's own gate children.
                draw_on_parent=False,
            )
        else:
            reservation = reserve(
                session,
                holder_label=label,
                footprint=footprint,
                pool=CapacityPool.HOST,
                holder_kind=DockerHolderKind.ORCHESTRATION,
                orchestration_run_id=orch_run.id,
                ticket_id=ticket.id,
                workspace_id=workspace.id,
                # The orchestrating process, so a dead driver's standing claim is
                # reclaimed at once rather than at TTL.
                holder_pid=os.getpid(),
                # Load-bearing: the six lease slots exist to space out heavy
                # work, and a run's *standing* claim must not crowd out the test
                # suites they are for.
                takes_slot=False,
            )
            if reservation.state is DockerGrantState.QUEUED:
                budget = (
                    settings.gate_capacity_wait_seconds if wait_seconds is None else wait_seconds
                )
                logger.warning(
                    "Run %s is waiting up to %.0fs for its %g-cpu parent host lease: %s",
                    orch_run.run_code,
                    budget,
                    cpus,
                    reservation.message,
                )
                reservation = wait_for_grant(session, reservation, budget=budget)

        if not reservation.granted:
            if reservation.lease_id:
                # A waiter nobody will ever poll holds a place in a strict
                # head-of-line queue, in front of work that would fit.
                release_lease(session, reservation.lease_id, reason=DockerLeaseEndReason.ABANDONED)
                reservation.lease_id = ""
            reservation.message = _unavailable_message(session, cpus, memory_mb, reservation)
            return reservation

        return _settle_duplicates(session, orch_run, reservation)


def _reused(session: Session, lease: DockerLease) -> DockerReservation:
    return DockerReservation(
        state=DockerGrantState.GRANTED,
        lease_id=lease.id,
        cpus=lease.cpus,
        memory_mb=lease.memory_mb,
        expires_at=as_utc(lease.expires_at),
        message=f"This run already holds parent lease {lease.id}.",
        reused=True,
        _session=session,
    )


def _settle_duplicates(
    session: Session, orch_run: OrchestrationRun, reservation: DockerReservation
) -> DockerReservation:
    """Keep the first standing claim for this run and give any other back.

    The in-process lock above cannot see a second process, and there is no
    unique constraint to lean on, so this is the repair rather than the
    prevention. The winner is deterministic from the rows alone — `position` is
    allocated by an atomic `UPDATE … RETURNING`, so the second committer always
    has the larger one — which is what lets two claimants agree without talking.
    """
    session.expire_all()
    rows = list(
        session.exec(
            select(DockerLease)
            .where(
                DockerLease.orchestration_run_id == orch_run.id,
                DockerLease.pool == CapacityPool.HOST,
                DockerLease.holder_kind == DockerHolderKind.ORCHESTRATION,
                col(DockerLease.agent_run_id).is_(None),
                col(DockerLease.status).in_(OCCUPYING),
            )
            .order_by(col(DockerLease.position), col(DockerLease.id))
        ).all()
    )
    if len(rows) <= 1:
        return reservation
    keeper, *extras = rows
    logger.warning(
        "Run %s booked %d standing host claims; keeping %s and handing the rest back",
        orch_run.run_code,
        len(rows),
        keeper.id,
    )
    for extra in extras:
        release_lease(session, extra.id, reason=DockerLeaseEndReason.RELEASED)
    if keeper.id == reservation.lease_id:
        return reservation
    return _reused(session, keeper)


def _unavailable_message(
    session: Session, cpus: float, memory_mb: int, reservation: DockerReservation
) -> str:
    """Why the parent could not be had, phrased for the person who must clear it.

    Names the pool and the ask, not the mechanism, and then what is holding it:
    "reserve returned exceeds_capacity" is not an instruction anyone can act on,
    and no agent can fix a busy machine.
    """
    holders = _host_holders(session)
    held = ", ".join(holders) if holders else "nothing the ledger can see"
    return (
        f"no {cpus:g}-cpu host capacity for this run's parent lease "
        f"({reservation.error_kind or 'still queued'}) — held now by: {held}. "
        f"{reservation.message} Nothing an agent can fix: this clears when those "
        "finish, or when a person releases one on Machine → Capacity."
    )


def _host_holders(session: Session) -> list[str]:
    """Live host-pool holders, biggest first, as an operator reads them."""
    session.expire_all()
    rows = session.exec(
        select(DockerLease).where(
            DockerLease.pool == CapacityPool.HOST,
            col(DockerLease.status).in_(OCCUPYING),
        )
    ).all()
    return [
        f"{lease.holder_label} ({lease.cpus:g} cpus)"
        for lease in sorted(rows, key=lambda row: (-row.cpus, row.holder_label))
    ]


def release_parent(
    session: Session,
    orch_run: OrchestrationRun,
    *,
    reason: DockerLeaseEndReason = DockerLeaseEndReason.RELEASED,
) -> bool:
    """Give this run's standing claim back, with any live child of it.

    Returns whether this call was the one that ended it. Idempotent on purpose:
    `execute()`'s `finally` runs after a park may already have released the same
    lease, and after the reaper may have. A pool credited twice admits past its
    own ceiling, and the symptom appears in somebody else's run.
    """
    lease = parent_lease_for(session, orch_run)
    if lease is None:
        return False
    return release_lease(session, lease.id, reason=reason)


def release_for_park(
    session: Session,
    orch_run: OrchestrationRun,
    ticket: Ticket,
    approval: Approval | None,
) -> bool:
    """Hold the parent across this park only when automation will resolve it.

    `approval` is None for the parks that have no gate row at all —
    `loregarden_mark_needs_person` and a block — and those are exactly the rows
    of the operator's table a predicate written around approvals forgets. There
    is nothing for any signer to own, so they release.
    """
    if approval is not None and signer_for_gate(session, ticket, orch_run, approval) is not None:
        return False
    return release_parent(session, orch_run, reason=DockerLeaseEndReason.PARK_RELEASED)


def release_leaked_gate_children(session: Session, parent_lease_id: str) -> int:
    """End any gate child of this parent left behind by an earlier transition.

    Nothing else recovers one. `reserve_child` refuses rather than queues, and
    `docker_reaper._judge` extends an expired child indefinitely while its
    parent is held — so a child left by a killed transition would refuse every
    later gate on the run, for the life of the run, with no clock to clear it.

    Narrowed to AD_HOC children: a nested child-ticket run's parent is a child
    of this one too, and it is live work rather than a leak.
    """
    leaked = session.exec(
        select(DockerLease).where(
            DockerLease.parent_lease_id == parent_lease_id,
            DockerLease.holder_kind == DockerHolderKind.AD_HOC,
            col(DockerLease.status).in_(OCCUPYING),
        )
    ).all()
    for lease in leaked:
        logger.warning(
            "Gate child %s (%s) outlived its transition; handing it back before the next gate",
            lease.id,
            lease.holder_label,
        )
        release_lease(session, lease.id, reason=DockerLeaseEndReason.RELEASED)
    return len(leaked)


def terminal_run_parents(session: Session) -> list[DockerLease]:
    """Standing claims whose orchestration is over — capacity nobody is using.

    `execute()`'s `finally` makes this unreachable through the driver, but a
    SIGKILL of the server skips every `finally` there is. Registered as an
    EXPECTED invariant for that reason (`invariant_registry`).
    """
    live = {OrchestrationRunStatus.RUNNING, OrchestrationRunStatus.QUEUED}
    rows = session.exec(
        select(DockerLease).where(
            DockerLease.pool == CapacityPool.HOST,
            DockerLease.holder_kind == DockerHolderKind.ORCHESTRATION,
            col(DockerLease.status).in_(OCCUPYING),
            col(DockerLease.orchestration_run_id).is_not(None),
        )
    ).all()
    stranded: list[DockerLease] = []
    for lease in rows:
        run = session.get(OrchestrationRun, lease.orchestration_run_id)
        if run is not None and run.status not in live:
            stranded.append(lease)
    return stranded


def release_terminal_run_parent(session: Session, lease_id: str) -> bool:
    """Repair for the invariant above: hand one stranded standing claim back."""
    lease = session.get(DockerLease, lease_id)
    if lease is None or lease.status not in OCCUPYING:
        return False
    if lease.status is DockerLeaseStatus.WAITING:
        return False
    return release_lease(session, lease_id, reason=DockerLeaseEndReason.RELEASED)
