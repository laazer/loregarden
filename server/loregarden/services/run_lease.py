"""A lease an agent run has to renew, so `RUNNING` can be disproved.

`RunStatus.RUNNING` was never evidence that anything was running. A run is
committed `RUNNING` before its subprocess exists, and if the supervising thread
dies the row reads `RUNNING` forever with nothing left to notice. Because the
status could not be disproved, `fail_interrupted_runs` never tried: it fails
*every* in-flight run with no liveness test, which is sound only at startup,
where "in flight" is provably a lie. That is why the whole reconciliation layer
was startup-only.

**Renewal, not inferred activity.** PR #159 was closed for inferring liveness
from observed `agent_runs` activity — an externally-harnessed stage produces
none, so a live external run looked dead. Inferring liveness from activity is
still trusting a claim. This asks instead whether the thing that owns the run
has said so, and treats any run kind with no defined renewer as alive.

**Renewed from the supervising thread, not from inside an adapter.** The obvious
place is each adapter's read loop, and it is the wrong one: there are three
execution paths (the CLI subprocess loop, the permission bridge's loop, and the
LM Studio HTTP runner), and a kind that renews in two of them is a kind whose
runs get killed on the third. The renewer here wraps `executor.execute` instead,
so it measures exactly what the lease is supposed to mean — the thread
supervising this run is still alive — for every adapter that exists now or later.
"""

from __future__ import annotations

import logging
import os
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from datetime import datetime, timedelta, timezone

from loregarden.db.session import engine
from loregarden.models.domain import AgentRun, RunStatus
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: How long a run may go unrenewed before its row stops counting as evidence.
#: Generous against the renewal interval below: a missed window costs a delay,
#: a false expiry kills work an agent is still doing.
AGENT_RUN_LEASE = timedelta(minutes=10)

#: How often the supervising thread stamps the lease while a run executes.
RENEWAL_INTERVAL_SECONDS = 30.0

#: A run in one of these has not finished, so its liveness is still a question.
IN_FLIGHT = (RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)

#: The statuses this predicate may judge. QUEUED is deliberately absent: a queued
#: run has no supervisor yet — `executor.execute` has not been entered, so
#: nothing is renewing — and it can legitimately wait far longer than the lease
#: for a slot. Judging it would reap exactly the runs that are waiting their
#: turn, which is the fail-closed rule applied to a kind that has no renewer
#: *yet* rather than none at all.
SUPERVISED = (RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION)


def pid_alive(pid: int) -> bool:
    """Whether `pid` is a live process on this host.

    Valid only because every run this control plane supervises — including a
    terminal handoff pasted into a shell — executes on the same machine as the
    control plane. There is no remote-execution path.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        # silent-ok: "no such process" IS the answer, not a failure to get one
        return False
    except PermissionError:
        # silent-ok: EPERM proves the process exists; ownership is not our concern
        return True
    except OSError:
        logger.warning(
            "Could not probe pid %s for liveness; treating it as dead", pid, exc_info=True
        )
        return False
    return True


def run_has_renewer(run: AgentRun) -> bool:
    """Whether this run's kind has something that renews its lease.

    The fail-closed half of the rule. A kind with no renewer must read as alive
    forever rather than as dead immediately, because silence from a thing that
    was never asked to speak is not evidence.

    An externally-harnessed run is the case that matters: the harness talks to
    the control plane at stage boundaries only, which is a report and not a
    heartbeat, so a stage that runs longer than the lease would look abandoned.
    Until that path gains a real check-in, its runs are not this predicate's to
    judge.
    """
    return run.external_harness is None


def agent_run_lease_expired(
    session: Session, run: AgentRun, *, lease: timedelta = AGENT_RUN_LEASE
) -> bool:
    """Whether this run has stopped being evidence that anything is running.

    Two decisive signals, in order:

    - **A recorded pid that is gone.** A terminal handoff stamps the shell pid
      it was pasted into; a pid that no longer exists settles the question
      outright, with no waiting.
    - **An expired lease**, for a kind that renews. A run that has never been
      renewed falls back to when it started, so a row written before the lease
      existed is judged rather than exempt.

    Anything else reads as alive: a finished run is not this function's
    business, and a kind with no renewer fails closed.
    """
    if run.status not in SUPERVISED:
        return False

    if run.handoff_pid is not None:
        return not pid_alive(run.handoff_pid)

    if not run_has_renewer(run):
        return False

    stamp = run.last_seen_at or run.started_at or run.created_at
    if stamp is None:
        return False
    if stamp.tzinfo is None:
        stamp = stamp.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - stamp > lease


def run_is_locally_supervised(run: AgentRun) -> bool:
    """Whether THIS process can see something working on this run.

    The third liveness policy in the codebase, and the one that used to be an
    inline clause in `drain.in_flight_runs` while its two siblings were named
    functions. Three policies is correct — the fail direction genuinely differs
    by purpose — but only two of them said so where a reader would look.

    FAILS OPEN, and that is the whole difference. `agent_run_lease_expired` fails
    CLOSED: a run it cannot judge reads as alive, because a reaper that guesses
    wrong kills work an agent is still doing. This one fails OPEN: a run it
    cannot judge is not waited on, because a drain that guesses wrong hangs a
    shutdown. Neither direction is safer in general; each is safer for its
    caller.

    It asks no lease question at all, which is the concrete consequence worth
    knowing: a CLI-kind run left RUNNING by an earlier crashed boot has a
    renewer and no pid, so this counts it and it can spend the whole drain
    window, where `stage_retry_budget._is_live_dispatch_evidence` would call it
    dead. Waiting on a run that has already stopped costs a slow shutdown; not
    waiting on one still working loses its output.

    The pid is asked separately from the renewer rather than folded into an
    `external_harness is not None` exclusion, because a recorded pid settles the
    question whatever the run's kind — that exclusion would drop the one external
    run whose process this machine can actually see. `pid_alive` rather than
    `handoff_pid is not None` for the same reason in reverse: a pid that is gone
    is the strongest evidence there is that nothing is running.
    """
    return run_has_renewer(run) or (run.handoff_pid is not None and pid_alive(run.handoff_pid))


def renew_agent_run_lease(run_id: str) -> None:
    """Stamp one run as still supervised.

    Its own short-lived session, for the reason `cancel_requested` uses one: the
    session driving the run may sit in a transaction old enough that its writes
    are not what a reader on another connection would see.
    """
    try:
        with Session(engine) as session:
            run = session.get(AgentRun, run_id)
            if run is None or run.status not in IN_FLIGHT:
                return
            run.last_seen_at = datetime.now(timezone.utc)
            session.add(run)
            session.commit()
    except Exception:  # noqa: BLE001 — a missed renewal is a delay, not a failure
        logger.warning("Could not renew lease for run %s", run_id, exc_info=True)


@contextmanager
def lease_renewal(
    run_id: str, *, interval_seconds: float = RENEWAL_INTERVAL_SECONDS
) -> Iterator[None]:
    """Renew `run_id`'s lease for as long as the body is executing.

    A daemon thread, so a supervising process that dies takes the renewal with
    it — which is the whole point. Nothing here can fail the run: the renewer
    stamps a column and is not part of the work.
    """
    stop = threading.Event()

    def _beat() -> None:
        renew_agent_run_lease(run_id)
        while not stop.wait(interval_seconds):
            renew_agent_run_lease(run_id)

    thread = threading.Thread(target=_beat, name=f"lease-{run_id[:8]}", daemon=True)
    thread.start()
    try:
        yield
    finally:
        stop.set()
        thread.join(timeout=5)
