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
    """Agent runs that have not reached a terminal status.

    Queued runs are not counted: nothing has started them, so there is nothing
    to wait for, and they keep their place for the next process.
    """
    return list(
        session.exec(select(AgentRun).where(col(AgentRun.status).in_(list(IN_FLIGHT)))).all()
    )


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
            "Drain: window closed after %.1fs with %d run(s) still in flight; "
            "they will be settled by the interruption path",
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
