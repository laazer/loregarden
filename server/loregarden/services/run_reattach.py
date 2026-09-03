"""Adopt a run whose agent outlived the server, instead of declaring it orphaned.

317 detaches the agent subprocess so a restart stops killing live turns. Without
this, detaching only leaks processes: the agent survives, and the next boot marks
its run interrupted anyway — so the work continues with nothing recording it.

`fail_interrupted_runs` fails every in-flight run with no liveness test. That was
sound while "in flight at startup" was provably a lie; detachment makes it false,
and a detached live run *is* sitting at RUNNING while the server boots. 469
shipped the predicate that disproves it — a pid plus a start-time fingerprint no
later process reusing that pid can match — and this is what consumes it.

The direction of the fail-closed matters and is inherited from
`process_identity.still_running`: a run with no recorded identity reads as *not*
surviving. A wrong "orphaned" costs one re-run; a wrong "still mine" means the
control plane adopts, reports on, and eventually signals a process it does not
own.
"""

from __future__ import annotations

import logging
import threading
import time

from loregarden.models.domain import AgentRun
from loregarden.services.process_identity import still_running
from loregarden.services.run_lease import (
    RENEWAL_INTERVAL_SECONDS,
    SUPERVISED,
    renew_agent_run_lease,
)
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def surviving_runs(session: Session) -> list[AgentRun]:
    """In-flight runs whose recorded process is still alive and still theirs.

    Externally-harnessed runs are excluded: they have no pid here to identify,
    and `fail_interrupted_runs` already leaves them alone on the boot path for
    the same reason. Including them would mean answering "did it survive" for a
    process on someone else's machine.
    """
    candidates = session.exec(
        select(AgentRun)
        .where(col(AgentRun.status).in_(list(SUPERVISED)))
        .where(col(AgentRun.external_harness).is_(None))
    ).all()
    return [run for run in candidates if still_running(run.agent_pid, run.agent_pid_identity)]


def _watch(run_id: str, pid: int, identity: str, interval_seconds: float) -> None:
    """Renew `run_id`'s lease while its process lives, then stop.

    Stopping is the half a naive renewer misses. `lease_renewal` ties renewal to
    a body executing in this process; a reattached run has no such body, so an
    unconditional renewer would hold a run alive forever after its agent died —
    turning a run this control plane could no longer see into one it swore was
    fine. When the process goes, renewal stops and `settle_expired_agent_runs`
    settles the row at the lease boundary, which is the honest outcome: nobody
    is supervising it any more.
    """
    while still_running(pid, identity):
        renew_agent_run_lease(run_id)
        time.sleep(interval_seconds)
    logger.info("Reattached run %s: its process is gone; renewal stopped", run_id[:8])


def reattach_surviving_runs(
    session: Session, *, interval_seconds: float = RENEWAL_INTERVAL_SECONDS
) -> list[AgentRun]:
    """Adopt every run whose agent outlived the restart. Returns what was adopted.

    Called before the boot reapers, which skip these runs on the strength of the
    same predicate. Each adopted run gets a daemon thread renewing its lease for
    as long as its process lives — without one it would be reaped ten minutes
    later by the very sweep this ticket exists to keep it away from.
    """
    adopted = surviving_runs(session)
    for run in adopted:
        renew_agent_run_lease(run.id)
        threading.Thread(
            target=_watch,
            args=(run.id, run.agent_pid, run.agent_pid_identity, interval_seconds),
            name=f"reattach-{run.id[:8]}",
            daemon=True,
        ).start()
        logger.info(
            "Reattached run %s (ticket %s, pid %s): its agent outlived the restart",
            run.run_code,
            run.ticket_id,
            run.agent_pid,
        )
    return adopted
