"""The reconciliation pass, and the two cadences it runs on.

Every sweep this control plane owns used to run in exactly one place: the
startup lifespan. That made repair a crash-recovery ritual rather than an
invariant — a lane wedged at 09:05 stayed wedged until someone restarted the
server.

One sweep escaped that and made it worse. `reconcile_lanes` had a second caller
on the queue status read path, and the dashboard polls it every few seconds, so
lane repair fired continuously *while someone was watching* and never otherwise.
Repair correlating with observation is the least useful cadence available: the
failure only survives when nobody is looking at it.

So the pass is named, and it runs on a timer as well as at boot. Two sets:

- **Periodic** — safe to run at any moment, against a live system. Each of these
  either consults a liveness predicate (`reconcile_lanes` via the orchestration
  lease) or selects only rows that nothing live can account for
  (`settle_stranded_stages` excludes any ticket with an in-flight run).

- **Startup only** — everything whose correctness depends on nothing being in
  flight. The `fail_interrupted_*` reapers assume every in-flight row is an
  orphan of the process that just died, which is true exactly once. Running them
  on a timer would kill live work, so ticket 437 requires that the timer skip
  them until 435 gives `agent_runs` a heartbeat that can disprove RUNNING.

`reconcile_worktrees` is startup-only for a different reason: it is the one step
that *deletes* something. It already refuses to touch a live tree for an
unfinished ticket, but its test is the ticket's state, and a ticket's state is a
derived value that has been wrong before. A status sweep that guesses wrong
writes a row somebody can fix; a worktree sweep that guesses wrong deletes work.
Boot is the only moment we know nothing is in flight, so that is where it stays.

Every step is best-effort and isolated: one raising is logged and the rest still
run, matching the shape `reconcile_slots` already had. A sweep that aborts
halfway leaves the system less consistent than one that never ran.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass

from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.run_service import (
    settle_expired_agent_runs,
    settle_expired_orchestration_leases,
    settle_orphaned_agent_runs,
    settle_stranded_stages,
)
from loregarden.services.ticket_rollup import reconcile_all_parents
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepStep:
    """One named unit of repair, so a failure can say which one failed."""

    name: str
    run: Callable[[Session], object]


def _settle_leases(session: Session) -> object:
    return settle_expired_orchestration_leases(session)


def _reconcile_lanes(session: Session) -> object:
    return QueueLaneService(session).reconcile_lanes()


#: Ordered, and the order is the same one the startup sequence established.
#: Agent runs first: a live agent run outranks the orchestration lease outright,
#: so an orchestration cannot be judged until the runs beneath it have been.
#: Then orchestration leases, because settling a run is what makes its lane
#: reclaimable; lanes next, so a freed lane can start what was waiting behind it;
#: parents last, so every child has reached its final state before it is
#: summarised.
#:
#: `settle_expired_agent_runs` is here and `fail_interrupted_runs` is not. They
#: look alike and are not: the first tests each run against a lease it has to
#: renew, the second assumes every in-flight row is an orphan of a process that
#: just died. Only the first can be told the truth by a run that is still working.
PERIODIC_STEPS: tuple[SweepStep, ...] = (
    SweepStep("settle_expired_agent_runs", settle_expired_agent_runs),
    SweepStep("settle_orphaned_agent_runs", settle_orphaned_agent_runs),
    SweepStep("settle_expired_orchestration_leases", _settle_leases),
    SweepStep("settle_stranded_stages", settle_stranded_stages),
    SweepStep("reconcile_lanes", _reconcile_lanes),
    SweepStep("reconcile_all_parents", reconcile_all_parents),
)


def reconcile_once(session: Session) -> list[str]:
    """Run every periodic sweep. Returns the names of the ones that raised.

    Never raises. A sweep is repair, and repair that can take the caller down
    with it is worse than no repair — on the timer it would end the loop, and at
    startup it would stop the server booting over a row nobody has looked at in
    a month.
    """
    failed: list[str] = []
    for step in PERIODIC_STEPS:
        try:
            step.run(session)
        except Exception:  # noqa: BLE001 — best-effort by design; see module docstring
            logger.exception("Reconciliation step %s failed; continuing", step.name)
            session.rollback()
            failed.append(step.name)
    return failed
