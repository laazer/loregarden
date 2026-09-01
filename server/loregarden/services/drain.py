"""Shutdown as a state the process passes through, not an event it suffers.

There is no drain today. A restart — and backend edits *require* one, so they
are frequent by design — takes every in-flight agent turn with it, and recovery
picks up the pieces on the next boot. `ReloadBlockedError` looks like a drain and
is not: it refuses the self-improve sentinel reload while runs are in flight,
which covers one restart trigger and says nothing about a crash, an operator
`task dev` cycle, or a SIGTERM.

Draining is two promises, and they are separate:

- **Nothing new starts.** Dispatch and queue pickup ask `is_draining()` and
  refuse, recording the reason on the queued entry. A silent skip would leave an
  entry that looks queued and is not.
- **What is running gets a bounded chance to land.** Bounded, because an
  unbounded wait is a hang, and a silent 90-second hang on shutdown is worse
  than the current behaviour. When the window closes, whatever is left goes
  through the *existing* interruption path.

That last point is the design rule: **drain improves the good case and must not
change the bad one.** A run that outlives the window is interrupted exactly as
it is interrupted today, so recovery has one path to reason about rather than
two, and a drain that fails leaves the system where it already knew how to be.

Refusal is deliberately not the same as failure. A queued entry refused during
drain keeps its place — it is still queued, and the next process will start it.
Only in-flight runs that miss the window are settled.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.db.session import engine
from loregarden.models.domain import AgentRun, RunStatus
from loregarden.services.run_lease import pid_alive, run_has_renewer
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

#: Set once shutdown begins. A process-wide flag rather than app state: the
#: callers that must consult it are service functions on worker threads, which
#: have no FastAPI request or app object to reach through.
_draining = threading.Event()

#: What a refused queue entry records, so an operator reading the board sees why
#: it did not start rather than an entry that looks stuck.
DRAIN_REFUSED_REASON = "Refused: the server is shutting down. This entry stays queued."

#: Statuses that still owe the process something before it can exit.
IN_FLIGHT = (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)


@dataclass(frozen=True)
class DrainReport:
    """What the wait actually achieved, for the log line and for tests."""

    started_with: int
    remaining: int
    waited_seconds: float

    @property
    def clean(self) -> bool:
        return self.remaining == 0


def begin_drain() -> None:
    """Refuse new work from here on. Idempotent."""
    if not _draining.is_set():
        logger.info("Draining: no new stage dispatch or queue pickup from here")
    _draining.set()


def end_drain() -> None:
    """Leave the draining state.

    Real processes do not come back from a drain — they exit. This exists for
    the reload path, which drains, finds the window closed with work still
    running, and refuses rather than restarting; a process that stays up must
    not stay refusing.
    """
    if _draining.is_set():
        logger.info("Drain cancelled: the server is staying up")
    _draining.clear()


def is_draining() -> bool:
    return _draining.is_set()


def in_flight_runs(session: Session) -> list[AgentRun]:
    """Agent runs this process must wait on before it may exit.

    Queued runs are not counted: nothing has started them, so there is nothing
    to wait for, and they keep their place for the next process.

    Neither is a run this process cannot judge — no *live* pid on this host and
    no lease renewer. Its RUNNING row says nothing about work happening here: an
    externally-harnessed stage lives in somebody else's terminal and routinely
    outlives the window by hours, so waiting on it spends the whole timeout and
    then warns about work that was never ours. The pid is asked separately from
    the renewer, rather than folded into an `external_harness is not None`
    exclusion, because a recorded pid settles the question whatever the run's
    kind — that exclusion would drop the one external run whose process this
    machine can actually see.

    `stage_retry_budget._is_live_dispatch_evidence` uses the same two primitives
    to a different end, and this is not that question. It gates on
    `agent_run_lease_expired` first and puts a ceiling on the no-renewer case,
    because a run it wrongly calls live is an unbounded bypass. This asks no
    lease question at all and fails *open* for a renewer kind: a CLI run left
    RUNNING by an earlier crashed boot has a renewer and no pid, so it is
    counted and can spend the whole window, where that predicate would call it
    dead. Waiting on a run that has already stopped costs a slow shutdown; not
    waiting on one that is still working loses its output.

    A recorded pid settles it *both ways*, which is why `pid_alive` is asked
    rather than `handoff_pid is not None`. A pid that is gone is the strongest
    evidence there is that nothing is running, and counting it spends the whole
    window on a dead process — the opposite of what the pid was recorded for.

    What the timeout warning may therefore promise: everything counted here is
    either a run the unscoped `fail_interrupted_runs` will claim on the next
    boot, or a run with a live pid on this host — an externally-harnessed stage
    genuinely working, which the boot sweep deliberately exempts and
    `settle_expired_agent_runs` settles through the lease once that pid goes
    away. Nothing counted here is left with no path to settlement.
    """
    running = session.exec(select(AgentRun).where(col(AgentRun.status).in_(list(IN_FLIGHT)))).all()
    return [
        run
        for run in running
        if run_has_renewer(run) or (run.handoff_pid is not None and pid_alive(run.handoff_pid))
    ]


def wait_for_quiescence(*, timeout_seconds: float, poll_seconds: float = 0.25) -> DrainReport:
    """Give in-flight runs until `timeout_seconds` to finish. Never raises.

    Polls rather than waits on a condition, because the runs it is waiting for
    finish on other threads and in other sessions — there is no single event to
    subscribe to, and inventing one would put drain-awareness into every
    completion path.

    Progress is logged: how many are in flight and how long it has waited. A
    shutdown that pauses silently is indistinguishable from a hang.
    """
    started = time.monotonic()
    with Session(engine) as session:
        started_with = len(in_flight_runs(session))

    if started_with == 0:
        logger.info("Drain: nothing in flight, exiting immediately")
        return DrainReport(started_with=0, remaining=0, waited_seconds=0.0)

    logger.info("Drain: waiting up to %.0fs for %d run(s) to land", timeout_seconds, started_with)
    remaining = started_with
    last_logged = started
    while time.monotonic() - started < timeout_seconds:
        with Session(engine) as session:
            remaining = len(in_flight_runs(session))
        if remaining == 0:
            break
        now = time.monotonic()
        if now - last_logged >= 5:
            logger.info("Drain: %d run(s) still in flight after %.0fs", remaining, now - started)
            last_logged = now
        time.sleep(poll_seconds)

    waited = time.monotonic() - started
    report = DrainReport(started_with=started_with, remaining=remaining, waited_seconds=waited)
    if report.clean:
        logger.info("Drain: all %d run(s) landed in %.1fs", started_with, waited)
    else:
        logger.warning(
            "Drain: window closed after %.1fs with %d supervised run(s) still in "
            "flight; recovery will settle them — the next boot's interruption "
            "sweep, or the lease for a harness still holding a live process here",
            waited,
            remaining,
        )
    return report


def refuse_reason() -> str:
    """The message a caller records when it declines to start something."""
    return DRAIN_REFUSED_REASON


def stamp_refusal(session: Session, entry, *, commit: bool = True) -> None:
    """Record on a queue entry that drain is why it did not start.

    The entry keeps its status: it is still queued, and the next process starts
    it. Only the reason is written, so the board can say why nothing is moving.
    """
    entry.failure_reason = DRAIN_REFUSED_REASON
    entry.last_failed_at = datetime.now(timezone.utc)
    session.add(entry)
    if commit:
        session.commit()
