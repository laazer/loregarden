"""Run one command while holding machine capacity: reserve, wait, run, release.

What `loregarden capacity run` does, and what a pre-push hook uses to stop
three repositories' test suites landing on one machine at once. The ledger
itself is `docker_leases`; this is a holder that blocks, which an MCP handler
may not but a shell process may.

**Waiting is polling.** The process keeps its own place in line alive (a
served poll is what the abandonment sweep looks for), drains the queue itself,
and runs a reap pass now and then — so a holder that died is reclaimed even
when the control-plane server, whose timer normally does that, is not running.

**Holding is a heartbeat.** A background thread renews the lease well inside
its TTL. The lease also names this process's pid, so a SIGKILL that skips the
`finally` still frees the capacity on the next sweep rather than at TTL.

**Failing to get capacity raises**, with the reason. Running the command
anyway is the caller's decision to make — the pre-push wrapper asks the person
at the keyboard — never this module's.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import threading
import time
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    DockerWaitBasis,
)
from loregarden.services.docker_capacity import resolve_weights
from loregarden.services.docker_leases import (
    DockerReservation,
    drain_waiters,
    refresh_ceiling,
    release_lease,
    renew_lease,
    reserve,
)
from loregarden.services.docker_ledger import (
    CHARGED_POOLS,
    OCCUPYING,
    as_utc,
    load_pool,
    pool_ceiling,
)
from loregarden.services.docker_poll_guard import note_poll
from loregarden.services.docker_reaper import reap_docker_leases
from loregarden.services.docker_wait_estimate import (
    UNKNOWN_WAIT,
    WaitEstimate,
    estimate_waits,
    poll_interval_for,
)
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session, col, func, select

logger = logging.getLogger(__name__)

#: What the command learns about its grant. Workers is the cpus rounded down,
#: never below one — what a test runner's `-n` / `--maxWorkers` should be.
LEASE_ENV = "LOREGARDEN_CAPACITY_LEASE_ID"
CPUS_ENV = "LOREGARDEN_CAPACITY_CPUS"
WORKERS_ENV = "LOREGARDEN_CAPACITY_WORKERS"

#: How often a waiting process runs a reap pass of its own. The server's timer
#: does this every 30s when it is up; this is the floor when it is not.
_REAP_INTERVAL_SECONDS = 60.0

Report = Callable[[str], None]
SessionFactory = Callable[[], Session]


class CapacityNotGranted(RuntimeError):
    """The ledger refused, could not be read, or the wait ran out."""


class _Terminated(BaseException):
    """SIGTERM/SIGHUP arrived before the command started.

    A BaseException, like KeyboardInterrupt, so no `except Exception` on the way
    out swallows it, and the `finally` that gives the place in line back runs.
    """

    def __init__(self, signum: int) -> None:
        super().__init__(signum)
        self.signum = signum


@dataclass(frozen=True)
class CapacityRequest:
    label: str
    pool: CapacityPool = CapacityPool.HOST
    footprint: DockerFootprint = DockerFootprint.CUSTOM
    cpus: float = 0.0
    memory_mb: int = 0
    #: Give up the place in line after this long. Zero waits only for an
    #: immediate grant.
    max_wait_seconds: float = 3600.0
    ttl_seconds: int | None = None
    workspace_id: str | None = None


def worker_count(cpus: float) -> int:
    return max(1, int(cpus))


def child_environment(reservation: DockerReservation, base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    env[LEASE_ENV] = reservation.lease_id
    env[CPUS_ENV] = f"{reservation.cpus:g}"
    env[WORKERS_ENV] = str(worker_count(reservation.cpus))
    return env


def fit_to_ceiling(session: Session, request: CapacityRequest, report: Report) -> tuple[float, int]:
    """The price to claim: the footprint's, or the whole pool when the machine is smaller.

    A per-repo footprint is chosen once and pushed from machines of every size.
    On one smaller than the footprint the claim could never be granted, and
    every push would stop at a prompt. Claiming the whole pool instead keeps the
    property that matters — nothing else heavy runs alongside — and says so.
    An unmeasurable pool is left alone: `reserve` refuses it with the reason.
    """
    cpus, memory_mb = resolve_weights(
        request.footprint, cpus=request.cpus, memory_mb=request.memory_mb
    )
    for pool in CHARGED_POOLS[request.pool]:
        ceiling = pool_ceiling(load_pool(session, pool))
        if not ceiling.known:
            ceiling = refresh_ceiling(session, pool_name=pool)
        if not ceiling.known or (cpus <= ceiling.cpus and memory_mb <= ceiling.memory_mb):
            continue
        fitted = (min(cpus, ceiling.cpus), min(memory_mb, ceiling.memory_mb))
        report(
            f"capacity: {cpus:g} cpus / {memory_mb} MB is more than this machine's whole "
            f"{pool.value} pool ({ceiling.cpus:g} cpus / {ceiling.memory_mb} MB); "
            f"claiming {fitted[0]:g} cpus / {fitted[1]} MB instead."
        )
        cpus, memory_mb = fitted
    return cpus, memory_mb


def acquire(
    session: Session,
    request: CapacityRequest,
    *,
    report: Report,
    sleep: Callable[[float], None] = time.sleep,
    clock: Callable[[], float] = time.monotonic,
) -> DockerReservation:
    """Hold capacity for `request`, waiting in line if need be. Raises when it cannot."""
    cpus, memory_mb = fit_to_ceiling(session, request, report)
    reservation = reserve(
        session,
        # The pid makes the label unique per process. Without it two pushes from
        # the same branch are one request to `_pending_duplicate`, which hands
        # both the same place in line — and then the same grant.
        holder_label=f"{request.label} · pid {os.getpid()}",
        footprint=request.footprint,
        cpus=cpus,
        memory_mb=memory_mb,
        ttl_seconds=request.ttl_seconds,
        holder_kind=DockerHolderKind.AD_HOC,
        workspace_id=request.workspace_id,
        holder_pid=os.getpid(),
        pool=request.pool,
    )
    if reservation.state is DockerGrantState.REJECTED:
        raise CapacityNotGranted(f"{reservation.error_kind}: {reservation.message}")
    if reservation.granted:
        return reservation
    return _wait_in_line(session, reservation, request, report=report, sleep=sleep, clock=clock)


def _wait_in_line(
    session: Session,
    reservation: DockerReservation,
    request: CapacityRequest,
    *,
    report: Report,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> DockerReservation:
    try:
        return _poll_until_granted(
            session, reservation, request, report=report, sleep=sleep, clock=clock
        )
    except (KeyboardInterrupt, _Terminated):
        # Stopped while queued: leave the line now rather than sitting at its
        # head until the abandonment sweep notices nobody is polling.
        release_lease(session, reservation.lease_id, reason=DockerLeaseEndReason.ABANDONED)
        raise


def _poll_until_granted(
    session: Session,
    reservation: DockerReservation,
    request: CapacityRequest,
    *,
    report: Report,
    sleep: Callable[[float], None],
    clock: Callable[[], float],
) -> DockerReservation:
    deadline = clock() + request.max_wait_seconds
    last_reap = clock()
    last_line = ""
    while True:
        session.expire_all()
        lease = session.get(DockerLease, reservation.lease_id)
        if lease is None or lease.status not in (DockerLeaseStatus.WAITING, *OCCUPYING):
            reason = "vanished" if lease is None else f"ended ({lease.end_reason})"
            raise CapacityNotGranted(f"lease {reservation.lease_id} {reason} while waiting")
        if lease.status in OCCUPYING:
            reservation.state = DockerGrantState.GRANTED
            reservation.position = None
            reservation.expires_at = as_utc(lease.expires_at)
            return reservation
        if clock() >= deadline:
            release_lease(session, lease.id, reason=DockerLeaseEndReason.ABANDONED)
            raise CapacityNotGranted(
                f"still queued after {request.max_wait_seconds:.0f}s; gave up the place in line"
            )

        note_poll(session, lease, min_interval=0)
        if clock() - last_reap >= _REAP_INTERVAL_SECONDS:
            reap_docker_leases(session)
            last_reap = clock()
        drain_waiters(session)

        estimate = estimate_waits(session).get(lease.id, UNKNOWN_WAIT)
        line = _queue_line(session, lease, estimate)
        if line != last_line:
            report(line)
            last_line = line
        session.commit()  # end any read transaction before sleeping on it
        sleep(max(1.0, min(poll_interval_for(estimate), deadline - clock())))


def _queue_line(session: Session, lease: DockerLease, estimate: WaitEstimate) -> str:
    ahead = session.exec(
        select(func.count())
        .select_from(DockerLease)
        .where(
            DockerLease.status == DockerLeaseStatus.WAITING,
            col(DockerLease.position) < lease.position,
        )
    ).one()
    if estimate.seconds is None:
        when = "no estimate yet"
    elif estimate.basis is DockerWaitBasis.TTL_BOUND:
        # A bound from the holders' TTLs, which they almost always beat.
        when = f"at most {estimate.seconds}s"
    else:
        when = f"about {estimate.seconds}s"
    return f"capacity: waiting for {lease.pool.value} capacity — {ahead} ahead, {when}"


class _Heartbeat:
    """Renews the lease from a thread of its own while the command runs."""

    def __init__(
        self, session_factory: SessionFactory, lease_id: str, interval: float, report: Report
    ) -> None:
        self._session_factory = session_factory
        self._lease_id = lease_id
        self._interval = interval
        self._report = report
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="capacity-heartbeat", daemon=True)

    def __enter__(self) -> _Heartbeat:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=self._interval + 5)

    def _run(self) -> None:
        while not self._stop.wait(self._interval):
            try:
                with self._session_factory() as session:
                    renewed = renew_lease(session, self._lease_id)
            except SQLAlchemyError as exc:
                # Transient (a locked database) is the common case; the next beat
                # retries, and the TTL is three beats long.
                self._report(f"capacity: could not renew lease {self._lease_id}: {exc}")
                continue
            if renewed is None:
                self._report(
                    f"capacity: lease {self._lease_id} was reclaimed while the command runs; "
                    "it is no longer holding capacity."
                )
                return


def run_holding(
    session_factory: SessionFactory,
    request: CapacityRequest,
    command: Sequence[str],
    *,
    report: Report,
    started_file: Path | None = None,
    environ: Mapping[str, str] | None = None,
    heartbeat_seconds: float | None = None,
) -> int:
    """Run `command` under a lease and return its exit status (128+N for signal N).

    A SIGTERM or SIGHUP before the command starts gives the place in line back
    and returns 128+N without starting it; once it runs, they are forwarded to it.
    """
    with _SignalRelay() as relay:
        try:
            with session_factory() as session:
                reservation = acquire(session, request, report=report)
        except _Terminated as stopped:
            return 128 + stopped.signum
        lease_id = reservation.lease_id
        report(
            f"capacity: holding {reservation.cpus:g} cpus / {reservation.memory_mb} MB ({lease_id})"
        )
        ttl = (
            reservation.expires_at - datetime.now(timezone.utc)
            if reservation.expires_at is not None
            else None
        )
        interval = heartbeat_seconds or max(1.0, ttl.total_seconds() / 3 if ttl else 60.0)
        try:
            with _Heartbeat(session_factory, lease_id, interval, report):
                if started_file is not None:
                    started_file.touch()
                code = relay.run(command, child_environment(reservation, environ or os.environ))
        except _Terminated as stopped:
            code = 128 + stopped.signum
        finally:
            _release(session_factory, lease_id, report)
    return code


class _SignalRelay:
    """Who a SIGTERM, SIGHUP or SIGINT is for, across the whole run.

    Installed before the reservation, so there is no moment a signal kills this
    process with a lease it has not released. Before the command starts, SIGTERM
    and SIGHUP raise `_Terminated` (SIGINT raises KeyboardInterrupt as usual),
    which unwinds through the release. Once it runs they are forwarded to it, and
    this process waits for it to exit so the release still happens. SIGINT is
    not forwarded: Ctrl-C reaches the whole foreground process group, so the
    command already has it, and a second copy reads to pytest as "force quit".
    """

    _FORWARDED = (signal.SIGTERM, signal.SIGHUP)

    def __init__(self) -> None:
        self._child: subprocess.Popen | None = None
        self._previous: dict[int, Any] = {}

    def __enter__(self) -> _SignalRelay:
        for sig in (*self._FORWARDED, signal.SIGINT):
            self._previous[sig] = signal.getsignal(sig)
        for sig in self._FORWARDED:
            signal.signal(sig, self._on_signal)
        return self

    def __exit__(self, *_exc: object) -> None:
        for sig, handler in self._previous.items():
            signal.signal(sig, handler)

    def _on_signal(self, number: int, _frame: object) -> None:
        if self._child is None:
            raise _Terminated(number)
        self._child.send_signal(number)

    def run(self, command: Sequence[str], env: Mapping[str, str]) -> int:
        signal.signal(signal.SIGINT, lambda _number, _frame: None)
        self._child = subprocess.Popen(list(command), env=dict(env))  # noqa: S603 — the caller's command
        code = self._child.wait()
        return 128 - code if code < 0 else code


def _release(session_factory: SessionFactory, lease_id: str, report: Report) -> None:
    try:
        with session_factory() as session:
            release_lease(session, lease_id)
    except SQLAlchemyError as exc:
        report(
            f"capacity: could not release lease {lease_id} ({exc}); it names pid {os.getpid()}, "
            "so the next reap sweep reclaims it once this process exits."
        )
