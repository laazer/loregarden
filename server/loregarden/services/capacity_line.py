"""Waiting in the capacity line for as long as it keeps moving.

`docker_leases.wait_for_grant` waits a fixed budget, then gives up its place.
That suits a caller that would rather fail fast than sit in line. A gate is not
one of those: lg-reference-sources-2018's survey→done gate waited its full 30
minutes behind an orderly line of test suites on 2026-11-04, gave its place
up, and the transition was blocked as if its work were wrong
(lg-machine-resource-2112). Nothing was wrong except the time.

So the limit here is on the line *not moving*, as `capacity_run` puts it for
the pre-push wrapper: the clock restarts whenever a lease ahead of this one
ends. Only a line where nothing ahead has finished for `stall_seconds` gives up,
and that is a holder that may be hung, which is a person's to look at.

Kept apart from `capacity_run`, whose queue machinery reaches `docker_reaper`
and from there the orchestrator, so the gate path can import this without a
cycle.
"""

from __future__ import annotations

import time
from collections.abc import Callable

from loregarden.models.domain import DockerGrantState, DockerLease, DockerLeaseStatus
from loregarden.services.docker_leases import (
    DockerReservation,
    drain_waiters,
    reclaim_dead_holders,
)
from loregarden.services.docker_ledger import OCCUPYING, as_utc
from loregarden.services.docker_poll_guard import note_poll
from sqlmodel import Session, col, or_, select

#: Between polls of our own place in line; what `wait_for_grant` uses too.
POLL_SECONDS = 2.0


def leases_ahead(session: Session, lease: DockerLease) -> dict[str, DockerLease]:
    """Every lease this one waits behind: those holding capacity, and waiters ahead.

    A waiter ahead that is granted stays in the set — it moved, but nothing
    freed up. Only a lease leaving the set means capacity came back.
    """
    rows = session.exec(
        select(DockerLease).where(
            or_(
                col(DockerLease.status).in_(OCCUPYING),
                (DockerLease.status == DockerLeaseStatus.WAITING)
                & (col(DockerLease.position) < lease.position),
            )
        )
    ).all()
    return {row.id: row for row in rows}


def wait_while_line_moves(
    session: Session,
    reservation: DockerReservation,
    *,
    stall_seconds: float,
    on_poll: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DockerReservation:
    """Poll our place in line until it is granted or the line stalls.

    Returns the reservation either way; the caller reads `granted`. `on_poll`
    runs on every pass, for a waiter whose own liveness is judged by a lease of
    its own (an orchestration run's): the thread waiting here is alive, and a
    wait longer than that lease must not read as the run having walked away.
    """
    moved_at = clock()
    ahead: set[str] = set()
    while True:
        reclaim_dead_holders(session)
        drain_waiters(session)
        session.expire_all()
        lease = session.get(DockerLease, reservation.lease_id)
        if lease is None or lease.status not in (DockerLeaseStatus.WAITING, *OCCUPYING):
            return reservation
        if lease.status in OCCUPYING:
            reservation.state = DockerGrantState.GRANTED
            reservation.position = None
            reservation.expires_at = as_utc(lease.expires_at)
            reservation.message = f"Granted {lease.cpus} cpus / {lease.memory_mb} MB."
            return reservation
        front = leases_ahead(session, lease)
        if ahead - front.keys():
            moved_at = clock()
        ahead = set(front)
        if clock() - moved_at >= stall_seconds:
            reservation.message = (
                f"nothing ahead in the line finished for {clock() - moved_at:.0f}s; still "
                f"holding: {', '.join(sorted(row.holder_label for row in front.values()))}"
            )
            return reservation
        note_poll(session, lease, min_interval=0)
        if on_poll is not None:
            on_poll()
        session.commit()  # end any read transaction before sleeping on it
        sleep(min(POLL_SECONDS, max(0.1, stall_seconds / 10)))
