"""Waiting in the capacity line for as long as it keeps moving.

This is the only in-process wait for capacity. There used to be a second,
`docker_leases.wait_for_grant`, which waited a fixed budget and then gave up its
place. lg-reference-sources-2018's survey→done gate waited its full 30 minutes
behind an orderly line of test suites on 2026-11-04, gave its place up, and the
transition was blocked as if its work were wrong (lg-machine-resource-2112).
Nothing was wrong except the time. That fix moved the gate here and left three
callers on the fixed budget: an agent run's host lease, an orchestration's parent
lease and a stage's docker lease. On 2026-11-06 a survey pass's agent run waited
its 30 minutes behind a moving line and was blocked the same way. The fixed-budget
wait is gone now, so no new caller can pick it.

So the limit here is on the line *not moving*, as `capacity_run` puts it for
the pre-push wrapper: the clock restarts whenever a lease ahead of this one
ends. Only a line where nothing ahead has finished for `stall_seconds` gives up,
and that is a holder that may be hung, which is a person's to look at.

Kept apart from `capacity_run`, whose queue machinery reaches `docker_reaper`
and from there the orchestrator, so the gate path can import this without a
cycle.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable

from loregarden.models.domain import (
    DockerGrantState,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    OrchestrationRun,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services.capacity_errors import CapacityWaitStopped
from loregarden.services.capacity_passing import is_standing
from loregarden.services.docker_leases import (
    DockerReservation,
    drain_waiters,
    reclaim_dead_holders,
    release_lease,
)
from loregarden.services.docker_ledger import OCCUPYING, as_utc
from loregarden.services.docker_poll_guard import note_poll
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, or_, select, update

logger = logging.getLogger(__name__)

#: Between polls of our own place in line.
POLL_SECONDS = 2.0

#: Between liveness writes while waiting: the lease's served poll, and
#: `on_poll`'s orchestration lease renewal. Reading the line every two seconds
#: is cheap; writing it was one SQLite write lock per waiter every two seconds,
#: exactly when the machine is busiest. Well inside every clock that reads the
#: stamp: the board flags a stalled waiter after 2 x
#: `docker_poll_max_interval_seconds` (240s), the sweep drops one after
#: `docker_waiting_ttl_seconds` (600s), and `ORCHESTRATION_LEASE` is 30 minutes.
HEARTBEAT_SECONDS = 60.0


def leases_ahead(session: Session, lease: DockerLease) -> dict[str, DockerLease]:
    """Every lease this one waits behind: those holding capacity, and waiters ahead.

    A waiter ahead that is granted stays in the set — it moved, but nothing
    freed up. Only a lease leaving the set means capacity came back.

    A standing claim that asked later and passed this one is not ahead of it: it
    was let through because it cannot delay it (`capacity_passing`). Counting it
    would also let a stream of agents finishing every few minutes read as the
    line moving, and a hung holder would never be reported.
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
    return {row.id: row for row in rows if not _passed(row, lease)}


def _passed(row: DockerLease, waiter: DockerLease) -> bool:
    return is_standing(row) and as_utc(row.requested_at) > as_utc(waiter.requested_at)


def wait_while_line_moves(
    session: Session,
    reservation: DockerReservation,
    *,
    stall_seconds: float,
    on_poll: Callable[[], None] | None = None,
    stop_requested: Callable[[], bool] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DockerReservation:
    """Poll our place in line until it is granted or the line stalls.

    Returns the reservation either way; the caller reads `granted`. `on_poll`
    runs every `HEARTBEAT_SECONDS`, for a waiter whose own liveness is judged by a lease of
    its own (an orchestration run's): the thread waiting here is alive, and a
    wait longer than that lease must not read as the run having walked away.

    `stop_requested` is asked on every poll; when it answers True the place in
    line is given back and `CapacityWaitStopped` raised. Without it a run
    stopped in line kept waiting for the whole stall window (up to an hour)
    with no process to signal, so the operator's stop did nothing
    (lg-code-knowledge-1765, 2026-11-19).
    """
    moved_at = clock()
    beat_at: float | None = None
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
        if stop_requested is not None and stop_requested():
            # Given back before raising: a waiter nobody polls holds the head
            # of a strict line, and its holder pid (this server) never dies.
            release_lease(session, lease.id, reason=DockerLeaseEndReason.ABANDONED)
            raise CapacityWaitStopped(
                f"{lease.holder_label} was stopped while it waited in line for capacity"
            )
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
        if beat_at is None or clock() - beat_at >= HEARTBEAT_SECONDS:
            beat_at = clock()
            note_poll(session, lease, min_interval=0)
            if on_poll is not None:
                on_poll()
        session.commit()  # end any read transaction before sleeping on it
        sleep(min(POLL_SECONDS, max(0.1, stall_seconds / 10)))


def occupying_lease_ids(session: Session, *, standing: set[str] | None = None) -> set[str]:
    """Every lease holding capacity now. One leaving the set means capacity came back.

    `standing` limits which standing claims count, to the ones already held when
    the wait began: later ones may pass the line, so their ending says nothing
    about whether what holds this claim back is moving.
    """
    rows = session.exec(select(DockerLease).where(col(DockerLease.status).in_(OCCUPYING))).all()
    return {
        row.id for row in rows if standing is None or row.id in standing or not is_standing(row)
    }


def retry_while_pool_moves(
    session: Session,
    attempt: Callable[[], DockerReservation],
    *,
    retryable: Callable[[DockerReservation], bool],
    waiting_for: str,
    stall_seconds: float,
    on_poll: Callable[[], None] | None = None,
    clock: Callable[[], float] = time.monotonic,
    sleep: Callable[[float], None] = time.sleep,
) -> DockerReservation:
    """Re-ask a claim that never takes a place in line until it is granted or the pool stalls.

    For a child lease (`capacity_children`): it is booked ahead of the queue or
    refused, and a refusal because the pool is full *right now* is not an answer
    about the work. lg-reference-sources-1912 and lg-milestone-that-1431 were
    both blocked on 2026-11-10 because their gate's 0.5-cpu excess did not fit
    for one instant while pre-push suites held the machine.

    The retry holds no place in line, so it cannot be the head anyone waits
    behind. The thread asking holds only its run's parent lease, and parents are
    sized so that every lane's parent together is well under the ceiling
    (`GatesConfig.parent_capacity_footprint`), so the capacity it waits for is
    held by work that finishes. The same stall rule as `wait_while_line_moves`
    bounds it: the clock restarts whenever a lease holding capacity ends, and
    only a pool where nothing has finished for `stall_seconds` gives up,
    returning the last refusal for the caller to report.
    """
    moved_at = clock()
    beat_at: float | None = None
    holding = at_start = occupying_lease_ids(session)
    warned = False
    while True:
        reservation = attempt()
        if reservation.granted or not retryable(reservation):
            return reservation
        if not warned and stall_seconds > 0:
            warned = True
            logger.warning(
                "%s is waiting for room (giving up only if nothing holding capacity "
                "finishes for %.0fs): %s",
                waiting_for,
                stall_seconds,
                reservation.message,
            )
        stalled_for = clock() - moved_at
        if stalled_for >= stall_seconds:
            if stall_seconds > 0:
                reservation.message = (
                    f"{reservation.message} Nothing holding capacity finished for "
                    f"{stalled_for:.0f}s while this waited for room."
                )
            return reservation
        if on_poll is not None and (beat_at is None or clock() - beat_at >= HEARTBEAT_SECONDS):
            beat_at = clock()
            on_poll()
        session.commit()  # end any read transaction before sleeping on it
        sleep(min(POLL_SECONDS, max(0.1, stall_seconds / 10)))
        reclaim_dead_holders(session)
        session.expire_all()
        now_holding = occupying_lease_ids(session, standing=at_start)
        if holding - now_holding:
            moved_at = clock()
        holding = now_holding


def renew_orchestration_lease(session: Session, orchestration_run_id: str) -> None:
    """The orchestration waiting in line is alive; say so on its lease.

    The lease is renewed at stage boundaries only, and a gate or a run
    waiting in line sits between two of them with no agent run in flight to vouch for it. Past
    `ORCHESTRATION_LEASE` the sweep would settle the run as abandoned while its
    driver was faithfully waiting — the lease-expiry half of run_f4bf51.
    """
    session.exec(
        update(OrchestrationRun)
        .where(col(OrchestrationRun.id) == orchestration_run_id)
        .values(last_seen_at=utcnow())
        .execution_options(synchronize_session=False)
    )


#: How often a held orchestration lease is renewed while long work runs inside a
#: stage boundary. A small fraction of `ORCHESTRATION_LEASE`, so a missed tick or
#: two still leaves the lease well inside its window.
KEEP_ALIVE_INTERVAL_SECONDS = 60.0


class OrchestrationLeaseKeepAlive:
    """Renew an orchestration's lease from a thread while its driver is busy.

    `renew_orchestration_lease` covers a driver *waiting in line*. A driver that
    holds capacity and runs a suite on its own thread — a land verification,
    whose commands took over 30 minutes for lg-code-knowledge-1055 — renewed
    nothing until the suite returned, so the sweep settled a live run as
    abandoned mid-landing ("driver is gone") while its tests were still running.

    Own sessions, one per tick: the renewal happens on another thread, and a
    session is not shared across threads. A failed tick is logged, not raised:
    the next tick retries, and the work it vouches for is unaffected.
    """

    def __init__(
        self,
        session_factory: Callable[[], Session],
        orchestration_run_id: str,
        *,
        interval: float | None = None,
    ) -> None:
        self._session_factory = session_factory
        self._orchestration_run_id = orchestration_run_id
        # Read per instance, not bound at definition, so one setting moves every
        # keep-alive (and a test can shorten it without a parameter here).
        self._interval = KEEP_ALIVE_INTERVAL_SECONDS if interval is None else interval
        self._stop = threading.Event()
        self._thread = threading.Thread(
            target=self._run, name="orchestration-keep-alive", daemon=True
        )

    def __enter__(self) -> OrchestrationLeaseKeepAlive:
        self.renew()
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            self.renew()

    def renew(self) -> None:
        """Stamp the run as alive now, committing so the sweep's session sees it."""
        try:
            with self._session_factory() as session:
                renew_orchestration_lease(session, self._orchestration_run_id)
                session.commit()
        except SQLAlchemyError:
            # Retryable: the next tick renews again, well inside the lease.
            logger.exception(
                "could not renew orchestration %s's lease; retrying in %ss",
                self._orchestration_run_id,
                self._interval,
            )
