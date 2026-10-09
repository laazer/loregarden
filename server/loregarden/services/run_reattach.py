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
`process_identity.liveness`: a run with no recorded identity reads as *not*
surviving. A wrong "orphaned" costs one re-run; a wrong "still mine" means the
control plane adopts, reports on, and eventually signals a process it does not
own.
"""

from __future__ import annotations

import logging
import threading

from loregarden.models.domain import AgentRun, ProcessState
from loregarden.services.process_identity import liveness
from loregarden.services.run_lease import (
    RENEWAL_INTERVAL_SECONDS,
    SUPERVISED,
    renew_agent_run_lease,
)
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


def _in_flight_by_state(session: Session) -> dict[ProcessState, list[AgentRun]]:
    """Every supervised in-flight run, grouped by what `ps` said about its process.

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
    grouped: dict[ProcessState, list[AgentRun]] = {state: [] for state in ProcessState}
    for run in candidates:
        grouped[liveness(run.agent_pid, run.agent_pid_identity)].append(run)
    return grouped


def surviving_runs(session: Session) -> list[AgentRun]:
    """In-flight runs whose recorded process is still alive and still theirs."""
    return _in_flight_by_state(session)[ProcessState.ALIVE]


def runs_to_spare(session: Session) -> list[AgentRun]:
    """In-flight runs a boot reaper must not fail: alive, or not knowably dead.

    A run whose `ps` could not answer is neither adopted — nothing vouches for
    the process — nor failed, which would be the reaper killing a working
    agent's row on a slow `ps` (lg-run-durability-862). It is left for the lease
    reaper, whose next sweep asks again; until then, the warning names it.
    """
    grouped = _in_flight_by_state(session)
    for run in grouped[ProcessState.UNKNOWN]:
        logger.warning(
            "Run %s (ticket %s, pid %s): could not tell whether its agent survived the "
            "restart; neither adopting nor failing it — the lease reaper will ask again",
            run.run_code,
            run.ticket_id,
            run.agent_pid,
        )
    return grouped[ProcessState.ALIVE] + grouped[ProcessState.UNKNOWN]


def _supervise(run_id: str, interval_seconds: float) -> None:
    """The thread's entry point: resume `run_id`, and report a crash as one.

    `run_resupervise` is imported HERE rather than at module scope, and not to
    dodge a cycle: it pulls in `OrchestrationService`, and this module is on the
    boot-reaper path that S6's split exists to keep orchestration off. Importing
    it at the top undoes the split while leaving every test of it green. The
    cost is one import inside a thread that is about to supervise a run for
    minutes to hours.

    An exception escaping a daemon thread reaches `threading.excepthook` and
    nothing else — no log line naming the run, no record on the row. A
    supervisor that died leaves its run at RUNNING for the lease reaper to
    settle, which is the honest fallback, but only if somebody can find out it
    happened.
    """
    from loregarden.services import run_resupervise

    try:
        run_resupervise.resupervise(run_id, interval_seconds=interval_seconds)
    except Exception:  # noqa: BLE001 — logged, and the lease reaper is the fallback
        logger.exception(
            "Resupervising run %s failed; it stays in flight for the lease reaper", run_id[:8]
        )


def reattach_surviving_runs(
    session: Session, *, interval_seconds: float = RENEWAL_INTERVAL_SECONDS
) -> list[AgentRun]:
    """Adopt every run whose agent outlived the restart. Returns what was adopted.

    Called before the boot reapers, which skip these runs on the strength of the
    same predicate. Each adopted run gets a daemon thread that RESUMES it — see
    `services.run_resupervise`. A thread that only renewed the lease was the
    half-measure this replaced: the agent finished, nothing read its exit status
    or its stage report, and the row settled at the lease boundary as
    un-supervised, so the stage never completed, committed or routed.
    """
    adopted = surviving_runs(session)
    for run in adopted:
        renew_agent_run_lease(run.id)
        threading.Thread(
            target=_supervise,
            args=(run.id, interval_seconds),
            name=f"resupervise-{run.id[:8]}",
            daemon=True,
        ).start()
        logger.info(
            "Reattached run %s (ticket %s, pid %s): its agent outlived the restart",
            run.run_code,
            run.ticket_id,
            run.agent_pid,
        )
    return adopted
