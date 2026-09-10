"""The docker capacity ledger: reserve, hold, hand back.

Shaped after `queue_admission.Reservation` because it solves the same problem
one resource over — *reserve, don't dispatch*. The caller gets a claim back and
starts its own containers; this service never runs docker, and could not, since
`docker_subprocess` refuses every verb that would.

**Granting happens in exactly one place.** `reserve` inserts a row as `WAITING`
and then runs `drain_waiters`, which is the only function that can move a lease
to `HELD`. Nothing anywhere does "read the totals, decide there is room, then
insert" — that is select-then-mutate, the defect `claim_free_slot` was written
to remove, and with two processes on one SQLite file (the server and the
in-process CLI) it double-books. The decision and the grant are one conditional
UPDATE whose WHERE clause carries every dimension at once, so the database picks
the winner.

**`bind` is not decoration.** Capacity has to be reserved *before*
`docker compose up` runs, and the project and container names only exist
*after*. Binding them is what upgrades a lease from "reapable when its clock
runs out" to "reapable when docker agrees nothing of it is running" — the whole
difference between a TTL that guesses and one that checks.

**Strict head-of-line.** Only the front of the queue is eligible, and the drain
stops at the first waiter that does not fit. A stream of small claims would
otherwise starve a large one indefinitely. Backfill is a real improvement and a
real source of starvation bugs; it is deliberately not here yet.
"""

from __future__ import annotations

import json
import logging
import time
from collections.abc import Iterator
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    DockerCapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    WorkflowStageDef,
)
from loregarden.models.domain.docker_tables import GLOBAL_POOL_ID
from loregarden.services.docker_capacity import (
    Ceiling,
    DockerInvoke,
    resolve_ceiling,
    resolve_weights,
)
from loregarden.services.docker_subprocess import run_docker
from pydantic import TypeAdapter, ValidationError
from sqlalchemy import bindparam, func, text, update
from sqlmodel import Session, select

logger = logging.getLogger(__name__)

#: Retries for a claim that lost a race rather than found the pool full. Named
#: for the same reason `parallel_queue._CLAIM_ATTEMPTS` is: the number is a
#: contention allowance, not a magic constant.
_CLAIM_ATTEMPTS = 4

#: How often a stage waiting for capacity re-checks its place in line.
_STAGE_POLL_SECONDS = 2.0

#: Why a request could not be granted, as a closed set the payloads quote.
REJECT_DISABLED = "capacity_ledger_disabled"
REJECT_DOCKER_UNAVAILABLE = "docker_unavailable"
REJECT_EXCEEDS_CAPACITY = "exceeds_capacity"
REJECT_UNKNOWN_LEASE = "unknown_lease"
REJECT_LEASE_NOT_HELD = "lease_not_held"

#: Statuses that count against the ceiling. `ORPHANED` is included on purpose:
#: its containers are confirmed to still be running, so the machine really is
#: that busy, and freeing it would over-book a box that is genuinely loaded.
OCCUPYING = (DockerLeaseStatus.HELD, DockerLeaseStatus.ORPHANED)


class DockerCapacityUnavailable(RuntimeError):
    """A stage that declared a docker footprint could not get one.

    Raised rather than returned, and rather than letting the stage run
    anyway: a ledger a caller may ignore when it is inconvenient is a report,
    not a limit. The run fails with a message naming what it wanted, which
    the existing stage-retry path handles as a dispatch failure.
    """


def _now() -> datetime:
    return datetime.now(timezone.utc)


#: The stored `container_names_json` payload, validated rather than
#: hand-inspected. It is written by this process, but it is still a blob coming
#: back out of a text column, and `isinstance(parsed, list)` is a schema check
#: written by hand — the thing the organization gate exists to stop.
_CONTAINER_NAMES = TypeAdapter(list[str])


def container_names(lease: DockerLease) -> list[str]:
    """The container names a lease recorded, or none if the column is unreadable.

    Unreadable is logged, not swallowed: a lease whose names cannot be parsed is
    reaped on its clock as though it had bound nothing, and that is a decision
    somebody should be able to find afterwards.
    """
    try:
        return _CONTAINER_NAMES.validate_json(lease.container_names_json or "[]")
    except ValidationError:
        logger.warning(
            "Lease %s has an unreadable container_names_json (%r); treating it as "
            "naming no containers, which means it will be reaped on its TTL alone",
            lease.id,
            lease.container_names_json,
        )
        return []


def as_utc(stamp: datetime | None) -> datetime | None:
    """Read a stored timestamp as UTC.

    SQLite gives back naive datetimes whatever went in, so a stored `expires_at`
    compared against `datetime.now(timezone.utc)` raises rather than answering.
    `run_lease.agent_run_lease_expired` normalises the same way, for the same
    reason; the alternative is a TypeError in the reaper, at the exact moment
    the reaper is the thing keeping the pool honest.
    """
    if stamp is None:
        return None
    return stamp.replace(tzinfo=timezone.utc) if stamp.tzinfo is None else stamp


@dataclass
class DockerReservation:
    """A granted claim, a place in line, or a refusal — and what to do next.

    Mirrors `queue_admission.Reservation`, including `reused`: a run that already
    holds a lease gets that one back rather than a second. One run holding two
    claims is the `AgentSlot` double-claim bug (lg-workflow-integrity-568) in a
    new table, and it is also what makes "release by run id" ambiguous. The
    ambiguity is removed by not creating it.
    """

    state: DockerGrantState
    lease_id: str = ""
    position: int | None = None
    ahead: int = 0
    cpus: float = 0.0
    memory_mb: int = 0
    expires_at: datetime | None = None
    message: str = ""
    error_kind: str = ""
    reused: bool = False
    _session: Session | None = field(default=None, repr=False)

    @property
    def granted(self) -> bool:
        return self.state is DockerGrantState.GRANTED

    def bind(self, *, compose_project: str = "", container_names: list[str] | None = None) -> None:
        """Record what the holder actually started.

        Until this lands the lease is reapable on its clock alone, because there
        is nothing docker could be asked about. Callers that start containers
        and never bind get TTL-only semantics — correct, but blunter than they
        need to be.
        """
        if not self.granted or not self._session:
            return
        names = list(container_names or [])
        lease = self._session.get(DockerLease, self.lease_id)
        if lease is None or lease.status is not DockerLeaseStatus.HELD:
            return
        if compose_project:
            lease.compose_project = compose_project
        if names:
            lease.container_names_json = json.dumps(names)
        self._session.add(lease)
        self._session.commit()

    def renew(self, *, ttl_seconds: int | None = None) -> datetime | None:
        """Push the expiry out. The heartbeat, for a holder with no run behind it."""
        if not self.granted or not self._session:
            return None
        expiry = renew_lease(self._session, self.lease_id, ttl_seconds=ttl_seconds)
        if expiry is not None:
            self.expires_at = expiry
        return expiry

    def release(self, *, reason: DockerLeaseEndReason = DockerLeaseEndReason.RELEASED) -> None:
        """Hand the capacity back. A no-op on a lease the reaper already ended.

        The same "still mine?" guard `Reservation.release` uses: releasing a
        lease that has already been settled would decrement the pool twice and
        let admission run past its own ceiling.
        """
        if not self.granted or not self._session:
            return
        release_lease(self._session, self.lease_id, reason=reason)

    def as_dict(self) -> dict:
        return {
            "state": self.state.value,
            "lease_id": self.lease_id,
            "position": self.position,
            "ahead": self.ahead,
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "expires_at": self.expires_at.isoformat() if self.expires_at else None,
            "renew_after_seconds": None,
            "message": self.message,
            "error_kind": self.error_kind,
            "reused": self.reused,
        }


# ---- pool primitives ---------------------------------------------------


def load_pool(session: Session) -> DockerCapacityPool:
    """The singleton, created on first use if the migration has not run yet.

    Lazy creation here is safe in a way it was NOT for `agent_slots`, and the
    difference is worth stating because the surface reads identically. That pool
    was keyed by `slot_number` with no unique constraint, so two threads
    initialising it each inserted a full set and the machine ran six agents
    against a limit of three. This row's identity is a constant primary key: two
    racers both inserting `'global'` means one insert and one integrity error,
    never two pools.

    The migration still seeds it, so a migrated database never reaches the
    fallback. It exists for the schema paths that skip migrations — `create_all`
    on a fresh database, and every test engine.
    """
    pool = session.get(DockerCapacityPool, GLOBAL_POOL_ID)
    if pool is not None:
        return pool

    session.add(DockerCapacityPool(id=GLOBAL_POOL_ID))
    session.commit()
    pool = session.get(DockerCapacityPool, GLOBAL_POOL_ID)
    if pool is None:  # pragma: no cover — the insert above either lands or raises
        raise RuntimeError("could not create the docker_capacity_pool singleton")
    return pool


def pool_ceiling(pool: DockerCapacityPool) -> Ceiling:
    return Ceiling(
        cpus=pool.ceiling_cpus,
        memory_mb=pool.ceiling_memory_mb,
        leases=pool.ceiling_leases,
        source=pool.ceiling_source,
        probed_at=pool.probed_at,
        error=pool.probe_error,
    )


def refresh_ceiling(
    session: Session, *, invoke: DockerInvoke = run_docker, use_cache: bool = True
) -> Ceiling:
    """Re-measure the machine and record the result on the pool row.

    Writes the ceiling into the pool so that every racer is judged by one
    number: if each claim carried its own locally cached measurement, two
    processes with different cache ages would enforce different limits.
    """
    previous = pool_ceiling(load_pool(session))
    ceiling = resolve_ceiling(invoke=invoke, previous=previous, use_cache=use_cache)
    pool = load_pool(session)
    pool.ceiling_cpus = ceiling.cpus
    pool.ceiling_memory_mb = ceiling.memory_mb
    pool.ceiling_leases = ceiling.leases
    pool.ceiling_source = ceiling.source
    pool.probed_at = ceiling.probed_at
    pool.probe_error = ceiling.error
    session.add(pool)
    session.commit()
    return ceiling


def repair_pool(session: Session) -> None:
    """Recompute the running totals from the ledger, which is authoritative.

    One statement, so it is atomic against a concurrent claim. This is what
    bounds the damage from a process killed between the pool update and the row
    commit: capacity leaks for one sweep interval rather than until a restart.
    """
    statement = text(
        """
        UPDATE docker_capacity_pool
        SET held_cpus = (SELECT COALESCE(SUM(cpus), 0) FROM docker_leases
                         WHERE status IN :occupying),
            held_memory_mb = (SELECT COALESCE(SUM(memory_mb), 0) FROM docker_leases
                              WHERE status IN :occupying),
            held_count = (SELECT COUNT(*) FROM docker_leases
                          WHERE status IN :occupying),
            revision = revision + 1
        WHERE id = :pool_id
        """
    ).bindparams(bindparam("occupying", expanding=True))
    session.connection().execute(
        statement,
        {"occupying": [status.value for status in OCCUPYING], "pool_id": GLOBAL_POOL_ID},
    )
    session.commit()


def _claim_capacity(session: Session, *, cpus: float, memory_mb: int, revision: int) -> bool:
    """Book `cpus`/`memory_mb` against the pool, or report that it did not fit.

    Every dimension is in the WHERE clause, so the database decides. Satisfying
    cpus while over-booking memory is not reachable from here — which it would
    be if the two were checked in Python and written afterwards.
    """
    result = session.exec(
        update(DockerCapacityPool)
        .where(DockerCapacityPool.id == GLOBAL_POOL_ID)
        .where(DockerCapacityPool.revision == revision)
        .where(DockerCapacityPool.held_cpus + cpus <= DockerCapacityPool.ceiling_cpus)
        .where(
            DockerCapacityPool.held_memory_mb + memory_mb <= DockerCapacityPool.ceiling_memory_mb
        )
        .where(DockerCapacityPool.held_count + 1 <= DockerCapacityPool.ceiling_leases)
        .values(
            held_cpus=DockerCapacityPool.held_cpus + cpus,
            held_memory_mb=DockerCapacityPool.held_memory_mb + memory_mb,
            held_count=DockerCapacityPool.held_count + 1,
            revision=DockerCapacityPool.revision + 1,
        )
        .execution_options(synchronize_session=False)
    )
    return bool(result.rowcount == 1)


def _at_least_zero(expression):
    """`MAX(expression, 0)` — SQLite's two-argument MAX, as a scalar."""
    return func.max(expression, 0)


def _return_capacity(session: Session, *, cpus: float, memory_mb: int) -> None:
    """Give booked capacity back, clamped at zero.

    Clamped because a double release must not drive the totals negative and
    hand out capacity that does not exist — the clamp turns a bug into a
    conservative number rather than an over-booking.
    """
    session.exec(
        update(DockerCapacityPool)
        .where(DockerCapacityPool.id == GLOBAL_POOL_ID)
        .values(
            held_cpus=_at_least_zero(DockerCapacityPool.held_cpus - cpus),
            held_memory_mb=_at_least_zero(DockerCapacityPool.held_memory_mb - memory_mb),
            held_count=_at_least_zero(DockerCapacityPool.held_count - 1),
            revision=DockerCapacityPool.revision + 1,
        )
        .execution_options(synchronize_session=False)
    )


# ---- the queue ---------------------------------------------------------


def _take_position(session: Session) -> int:
    """The next place in line, incremented and read in one statement.

    `UPDATE … RETURNING` rather than compare-and-set on the value read first.
    The CAS version was correct but not live: under an eight-way arrival it
    exhausted its retries and raised, turning contention — the one condition
    this whole feature exists to handle — into a crashed reservation. An atomic
    increment cannot lose a race, so it needs no retries and has no give-up path.
    """
    row = (
        session.connection()
        .execute(
            text(
                "UPDATE docker_capacity_pool SET next_position = next_position + 1 "
                "WHERE id = :pool_id RETURNING next_position"
            ),
            {"pool_id": GLOBAL_POOL_ID},
        )
        .one_or_none()
    )
    session.commit()
    if row is None:  # pragma: no cover — load_pool guarantees the row exists
        raise RuntimeError("docker_capacity_pool disappeared while claiming a queue position")
    # RETURNING gives the value after the update, so the position handed out is
    # the one before it.
    return int(row[0]) - 1


def _waiters(session: Session) -> list[DockerLease]:
    return list(
        session.exec(
            select(DockerLease)
            .where(DockerLease.status == DockerLeaseStatus.WAITING)
            .order_by(DockerLease.position)
        ).all()
    )


def drain_waiters(session: Session) -> list[str]:
    """Promote the front of the queue for as long as it fits. The only granter.

    Strictly head-of-line: the walk stops at the first waiter that does not fit
    rather than looking past it for something smaller. Skipping is how a stream
    of light claims starves a heavy one forever, and a queue that is unfair is
    harder to reason about than one that is merely slow.

    Returns the ids promoted, so a caller can tell an operator what its release
    set moving.
    """
    # The pool moves under this session whenever anything else grants or
    # releases; a promotion decided from a cached read would compare against a
    # revision that no longer exists and give up believing the pool was full.
    session.expire_all()
    promoted: list[str] = []
    for lease in _waiters(session):
        # Capacity first, then the row — and the order is the whole correctness
        # argument, arrived at by getting it wrong twice.
        #
        # Booking without claiming the row lets two concurrent drains (and every
        # reserve runs one) each book capacity for the same waiter: measured at
        # `held_count=3` against two HELD rows in 5 of 12 eight-way runs, which
        # leaks a unit of capacity permanently.
        #
        # Claiming the row first, by flipping it to HELD as a lock, is worse. A
        # peer's `reserve` reads that row before the booking resolves and
        # reports GRANTED to a caller whose lease is about to go back into the
        # queue — telling somebody they may start containers on capacity they do
        # not hold. Over-reporting beat under-reporting in 5 of 12 runs too.
        #
        # So: book, then compare-and-set the row, then refund if the row was
        # lost. The refund window over-counts the pool for an instant, which
        # errs towards refusing a claim rather than double-granting one.
        if not _book_capacity_for(session, lease):
            break  # the head does not fit, which is what head-of-line means

        if not _claim_promotion(session, lease):
            _return_capacity(session, cpus=lease.cpus, memory_mb=lease.memory_mb)
            session.commit()
            continue

        promoted.append(lease.id)
    return promoted


def _claim_promotion(session: Session, lease: DockerLease) -> bool:
    """Grant one waiter, or report that a peer got there first.

    The `status == WAITING` predicate is the compare-and-set: exactly one drain
    can move a given row out of the queue, so the capacity already booked either
    belongs to this promotion or is handed straight back. Everything a granted
    lease needs is written in the same statement, so no observer can see a HELD
    row with no expiry.
    """
    now = _now()
    result = session.exec(
        update(DockerLease)
        .where(DockerLease.id == lease.id)
        .where(DockerLease.status == DockerLeaseStatus.WAITING)
        .values(
            status=DockerLeaseStatus.HELD,
            granted_at=now,
            last_renewed_at=now,
            expires_at=now + timedelta(seconds=lease.ttl_seconds),
            position=0,
        )
        .execution_options(synchronize_session=False)
    )
    session.commit()
    return bool(result.rowcount == 1)


def _book_capacity_for(session: Session, lease: DockerLease) -> bool:
    """Book this lease's price against the pool, retrying only real contention.

    A failed conditional UPDATE means either "the pool moved under this read" or
    "it is genuinely full". Re-reading and re-testing the *numbers* answers that
    directly; the revision counter alone cannot, because it moves for reasons
    that have nothing to do with whether this claim fits.
    """
    for _ in range(_CLAIM_ATTEMPTS):
        session.expire_all()
        pool = load_pool(session)
        if _claim_capacity(
            session, cpus=lease.cpus, memory_mb=lease.memory_mb, revision=pool.revision
        ):
            session.commit()
            return True
        session.rollback()
        session.expire_all()
        fresh = load_pool(session)
        if not _fits(fresh, lease):
            return False  # full, not contended — retrying would only spin
    return False


def _fits(pool: DockerCapacityPool, lease: DockerLease) -> bool:
    return (
        pool.held_cpus + lease.cpus <= pool.ceiling_cpus
        and pool.held_memory_mb + lease.memory_mb <= pool.ceiling_memory_mb
        and pool.held_count + 1 <= pool.ceiling_leases
    )


def _held_for_run(
    session: Session, *, agent_run_id: str | None, orchestration_run_id: str | None
) -> DockerLease | None:
    """A lease this run already holds, if any."""
    if not agent_run_id and not orchestration_run_id:
        return None
    statement = select(DockerLease).where(DockerLease.status.in_(OCCUPYING))
    if agent_run_id:
        statement = statement.where(DockerLease.agent_run_id == agent_run_id)
    else:
        statement = statement.where(DockerLease.orchestration_run_id == orchestration_run_id)
    return session.exec(statement).first()


def reserve(
    session: Session,
    *,
    holder_label: str,
    footprint: DockerFootprint = DockerFootprint.CUSTOM,
    cpus: float = 0.0,
    memory_mb: int = 0,
    ttl_seconds: int | None = None,
    holder_kind: DockerHolderKind = DockerHolderKind.AD_HOC,
    agent_run_id: str | None = None,
    orchestration_run_id: str | None = None,
    ticket_id: str | None = None,
    workspace_id: str | None = None,
    holder_pid: int | None = None,
    invoke: DockerInvoke = run_docker,
) -> DockerReservation:
    """Claim docker capacity, take a place in line, or be told why neither.

    Refusals are returned, not raised, and each carries an `error_kind` — an
    agent that cannot tell "wait your turn" from "this will never fit" will do
    the wrong one of the two.
    """
    if not settings.docker_capacity_enabled:
        return DockerReservation(
            state=DockerGrantState.REJECTED,
            error_kind=REJECT_DISABLED,
            message="The docker capacity ledger is disabled; nothing is being tracked.",
        )

    price_cpus, price_memory = resolve_weights(footprint, cpus=cpus, memory_mb=memory_mb)

    ceiling = pool_ceiling(load_pool(session))
    if not ceiling.known:
        ceiling = refresh_ceiling(session, invoke=invoke)
    if not ceiling.known:
        # Fail closed. A ledger that admits freely because it cannot see the
        # machine is worse than one that says it cannot see the machine.
        return DockerReservation(
            state=DockerGrantState.REJECTED,
            error_kind=REJECT_DOCKER_UNAVAILABLE,
            message=f"Cannot measure docker capacity, so nothing can be booked: {ceiling.error}",
        )

    if price_cpus > ceiling.cpus or price_memory > ceiling.memory_mb or ceiling.leases < 1:
        # Refused rather than queued: parking a claim that can never be granted
        # would block the head of the line for everything behind it, forever.
        return DockerReservation(
            state=DockerGrantState.REJECTED,
            error_kind=REJECT_EXCEEDS_CAPACITY,
            cpus=price_cpus,
            memory_mb=price_memory,
            message=(
                f"A claim of {price_cpus} cpus / {price_memory} MB exceeds the whole "
                f"ceiling ({ceiling.cpus} cpus / {ceiling.memory_mb} MB, "
                f"{ceiling.leases} leases)."
            ),
        )

    existing = _held_for_run(
        session, agent_run_id=agent_run_id, orchestration_run_id=orchestration_run_id
    )
    if existing is not None:
        return DockerReservation(
            state=DockerGrantState.GRANTED,
            lease_id=existing.id,
            cpus=existing.cpus,
            memory_mb=existing.memory_mb,
            expires_at=as_utc(existing.expires_at),
            message=f"This run already holds lease {existing.id}.",
            reused=True,
            _session=session,
        )

    ttl = _clamp_ttl(ttl_seconds)
    lease = DockerLease(
        status=DockerLeaseStatus.WAITING,
        holder_kind=holder_kind,
        holder_label=holder_label,
        agent_run_id=agent_run_id,
        orchestration_run_id=orchestration_run_id,
        ticket_id=ticket_id,
        workspace_id=workspace_id,
        holder_pid=holder_pid,
        footprint=footprint,
        cpus=price_cpus,
        memory_mb=price_memory,
        position=_take_position(session),
        ttl_seconds=ttl,
    )
    session.add(lease)
    session.commit()

    drain_waiters(session)
    # Another claimant's drain may have promoted this lease between the insert
    # and here. Expiring first means the answer reflects that rather than this
    # session's pre-drain snapshot — a caller told "queued" while it in fact
    # holds the lease will sit and poll for something it already has.
    session.expire_all()
    session.refresh(lease)

    if lease.status is DockerLeaseStatus.HELD:
        return DockerReservation(
            state=DockerGrantState.GRANTED,
            lease_id=lease.id,
            cpus=lease.cpus,
            memory_mb=lease.memory_mb,
            expires_at=as_utc(lease.expires_at),
            message=f"Granted {lease.cpus} cpus / {lease.memory_mb} MB.",
            _session=session,
        )

    ahead = sum(1 for other in _waiters(session) if other.position < lease.position)
    return DockerReservation(
        state=DockerGrantState.QUEUED,
        lease_id=lease.id,
        position=lease.position,
        ahead=ahead,
        cpus=lease.cpus,
        memory_mb=lease.memory_mb,
        message=(
            f"Docker capacity is full. Queued at position {lease.position} "
            f"with {ahead} ahead. Waiting is not a failure — poll for this lease."
        ),
        _session=session,
    )


def _clamp_ttl(ttl_seconds: int | None) -> int:
    requested = settings.docker_lease_ttl_seconds if ttl_seconds is None else ttl_seconds
    return max(1, min(int(requested), settings.docker_lease_max_ttl_seconds))


def renew_lease(
    session: Session, lease_id: str, *, ttl_seconds: int | None = None
) -> datetime | None:
    """Push a held lease's expiry out. Returns None when there is nothing to renew.

    None rather than an exception because the common cause is a lease the
    reaper already settled, which the caller has to handle either way — and it
    is distinguishable from success, which is what the rule actually requires.
    """
    lease = session.get(DockerLease, lease_id)
    if lease is None or lease.status not in OCCUPYING:
        return None
    now = _now()
    if ttl_seconds is not None:
        lease.ttl_seconds = _clamp_ttl(ttl_seconds)
    lease.last_renewed_at = now
    lease.expires_at = now + timedelta(seconds=lease.ttl_seconds)
    # A renewal is evidence the holder is alive, which is exactly what an
    # orphaned lease was missing. Let it back into the normal lifecycle.
    if lease.status is DockerLeaseStatus.ORPHANED:
        lease.status = DockerLeaseStatus.HELD
    session.add(lease)
    session.commit()
    return as_utc(lease.expires_at)


def release_lease(
    session: Session,
    lease_id: str,
    *,
    reason: DockerLeaseEndReason = DockerLeaseEndReason.RELEASED,
    drain: bool = True,
) -> bool:
    """Hand capacity back and start whatever was waiting for it.

    Idempotent, and deliberately so: a lease the reaper has already settled must
    not be settled again, or the pool is decremented twice and admission runs
    past its own ceiling. Returns whether this call was the one that ended it.

    `drain=False` is for a caller releasing several leases in one pass: it wants
    one promotion at the end, and it wants to *see* what that promotion did. A
    drain here would promote waiters into a list the caller never receives, and
    the sweep would then report having promoted nothing while having promoted
    something — a report that is wrong in the quiet direction.
    """
    lease = session.get(DockerLease, lease_id)
    if lease is None:
        return False
    if lease.status not in OCCUPYING:
        if lease.status is not DockerLeaseStatus.WAITING:
            return False
        lease.status = DockerLeaseStatus.RELEASED
        lease.released_at = _now()
        lease.end_reason = reason
        lease.position = 0
        session.add(lease)
        session.commit()
        return True

    lease.status = DockerLeaseStatus.RELEASED
    lease.released_at = _now()
    lease.end_reason = reason
    session.add(lease)
    _return_capacity(session, cpus=lease.cpus, memory_mb=lease.memory_mb)
    session.commit()

    if drain:
        drain_waiters(session)
    return True


@contextmanager
def docker_capacity_for_stage(
    session: Session,
    run: AgentRun,
    stage: WorkflowStageDef,
    *,
    wait_seconds: float | None = None,
) -> Iterator[DockerReservation | None]:
    """Hold docker capacity for as long as this stage's agent is executing.

    Yields `None` — immediately, and after one enum comparison — for a stage
    that declared no footprint. That is nearly every stage, and it is why the
    opt-in is per stage rather than per workspace: a lease held by a planning
    stage is capacity taken from the test stage waiting behind it.

    Scoped to the same block `run_lease.lease_renewal` wraps, deliberately. That
    contextmanager exists because the supervising thread is the one thing that
    means "this run is alive" across all three execution paths; a docker lease
    with the same lifetime inherits that liveness instead of carrying a second
    heartbeat that can drift out of step with the first. The reaper knows: a
    lease bound to a live run is extended without asking docker at all.

    **The stage waits, bounded, and then fails rather than proceeding.** The
    orchestrator is a background thread, so it may block where an MCP handler
    may not — but running the stage anyway when capacity was refused would make
    the ledger advisory, and a ledger nobody has to obey is a report, not a
    limit.
    """
    footprint = stage.docker_footprint
    if footprint is DockerFootprint.NONE or not settings.docker_capacity_enabled:
        yield None
        return

    budget = settings.docker_stage_wait_seconds if wait_seconds is None else wait_seconds
    reservation = reserve(
        session,
        holder_label=f"stage {stage.key} ({run.agent_id})",
        footprint=footprint,
        cpus=stage.docker_cpus,
        memory_mb=stage.docker_memory_mb,
        holder_kind=DockerHolderKind.AGENT_RUN,
        agent_run_id=run.id,
        orchestration_run_id=run.orchestration_run_id,
        ticket_id=run.ticket_id,
        workspace_id=run.workspace_id,
    )

    if reservation.state is DockerGrantState.QUEUED:
        reservation = _wait_for_capacity(session, reservation, budget=budget)

    if not reservation.granted:
        # Give the place in line back rather than leaving a waiter nobody will
        # ever poll for — it would sit at the head of the queue until the
        # reaper's abandonment sweep, blocking everything behind it.
        if reservation.lease_id:
            release_lease(session, reservation.lease_id, reason=DockerLeaseEndReason.ABANDONED)
        raise DockerCapacityUnavailable(
            f"Stage {stage.key} needs {footprint.value} docker capacity and did not get it "
            f"within {budget:.0f}s: {reservation.message}"
        )

    try:
        yield reservation
    finally:
        reservation.release(reason=DockerLeaseEndReason.RUN_COMPLETED)


def _wait_for_capacity(
    session: Session, reservation: DockerReservation, *, budget: float
) -> DockerReservation:
    """Poll our own place in line until it is granted or the budget runs out."""
    deadline = time.monotonic() + budget
    while time.monotonic() < deadline:
        time.sleep(min(_STAGE_POLL_SECONDS, max(0.1, budget / 10)))
        drain_waiters(session)
        session.expire_all()
        lease = session.get(DockerLease, reservation.lease_id)
        if lease is not None and lease.status is DockerLeaseStatus.HELD:
            reservation.state = DockerGrantState.GRANTED
            reservation.position = None
            reservation.expires_at = as_utc(lease.expires_at)
            reservation.message = f"Granted {lease.cpus} cpus / {lease.memory_mb} MB."
            return reservation
    return reservation
