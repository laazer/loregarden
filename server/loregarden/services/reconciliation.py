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

from loregarden.services.chat_feature_loop import advance_feature_loops
from loregarden.services.chat_wait_watch import poll_chat_waits
from loregarden.services.dependency_release import mark_parked_waits
from loregarden.services.docker_reaper import reap_docker_leases
from loregarden.services.integration_branch_health import check_integration_branches
from loregarden.services.invariant_registry import (
    AWAITING_STAGES_ARE_ASKED,
    CLOSED_TICKETS_HAVE_CLOSED_WORKFLOWS,
    EVERY_BLOCK_IS_SETTLED,
    EXHAUSTED_GATES_BLOCK,
    HELD_REPAIRS_READ_IN_PROGRESS,
    HELD_TICKETS_RESUME,
    LANDED_WORK_IS_COMMITTED,
    LANDING_BLOCKS_CLEAR,
    LIVE_WORKSPACES_HAVE_REAL_REPOS,
    NO_TICKET_DEPENDS_ON_AN_ANCESTOR,
    PUBLISH_CARDS_CLOSE,
    SURVEY_CARDS_CLOSE,
    TICKET_BRANCHES_HOLD_HISTORY,
)
from loregarden.services.invariants import Invariant, run_invariant
from loregarden.services.network_wait_resume import resume_network_waits
from loregarden.services.parked_stage_resume import (
    resume_stranded_stages,
    resume_usage_limit_waits,
)
from loregarden.services.queue_lanes import QueueLaneService
from loregarden.services.run_service import (
    settle_expired_agent_runs,
    settle_expired_orchestration_leases,
    settle_orphaned_agent_runs,
    settle_stranded_stages,
)
from loregarden.services.terminal_stage_sweep import finish_parked_terminal_tickets
from loregarden.services.ticket_rollup import reconcile_all_parents
from loregarden.services.workflow_monitor import sweep as monitor_sweep
from sqlmodel import Session

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class SweepStep:
    """One named unit of repair, so a failure can say which one failed."""

    name: str
    run: Callable[[Session], object]


def _invariant(invariant: Invariant) -> SweepStep:
    """A registered invariant as a step, under the invariant's own name."""

    def run(session: Session) -> object:
        return run_invariant(session, invariant)

    return SweepStep(invariant.name, run)


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
    # Before the lanes are driven: a hold whose ticket still reads blocked is
    # re-armed here, so the same pass's re-dispatch runs the stage (1656).
    _invariant(HELD_REPAIRS_READ_IN_PROGRESS),
    SweepStep("reconcile_lanes", _reconcile_lanes),
    # Periodic by the same rule: it selects only tickets with no agent run and
    # no orchestration behind them. It sits after the settlers so a stage they
    # just settled is read at its new status, and before `reconcile_all_parents`
    # so a ticket this finishes is summarised by its parent in the same pass.
    SweepStep("finish_parked_terminal_tickets", finish_parked_terminal_tickets),
    # Same selection rule (nothing live behind the ticket), for the BLOCKED
    # terminal stage the step above leaves alone. It acts only when a read-only
    # merge preview says the landing would now succeed, so a conflict that is
    # still there is never retried (928).
    _invariant(LANDING_BLOCKS_CLEAR),
    SweepStep("reconcile_all_parents", reconcile_all_parents),
    # After the parents settle, so a feature ticket finished this pass is read
    # as done: its chat loop marks the round met and starts the queued one.
    SweepStep("advance_feature_loops", advance_feature_loops),
    # Periodic by the same rule as the steps above: it consults liveness
    # predicates — a pid, the run behind the lease, and docker itself — rather
    # than assuming everything in flight is an orphan. It also never frees a
    # lease it could not verify, so a sweep during a docker restart is a no-op
    # rather than a mass reclaim.
    SweepStep("reap_docker_leases", reap_docker_leases),
    # Same liveness rule as docker reaping: identify() is a predicate, UNKNOWN
    # is never GONE, and a sweep during a pid-lookup failure is a skip rather
    # than a drop. resume_chat_waits stays boot-only.
    SweepStep("poll_chat_waits", poll_chat_waits),
    # Report-only: this one observes and writes findings, it repairs nothing.
    # It rides the existing timer rather than adding a loop, and the wrapper
    # above means a bad scan cannot take the repair sweeps down with it.
    SweepStep("scan_workflow_monitor", monitor_sweep),
    # Blocks, and settles, the tickets an exhausted gate parked before its
    # writer blocked them (lg-milestone-that-1432).
    _invariant(EXHAUSTED_GATES_BLOCK),
    # After the settlers, because they are what writes most of the harness
    # blocks this classifies: a lease expiry settled two steps up gets its
    # kind here, in the same pass, without the settler having to know (749).
    _invariant(EVERY_BLOCK_IS_SETTLED),
    # Periodic by the same rule: it acts only on tickets still blocked on the
    # stage a network wait parked, with no orchestration behind them, and only
    # once the provider answers. It probes nothing when nothing is waiting.
    SweepStep("resume_network_waits", resume_network_waits),
    # Same selection rule, for the two other causes that clear by themselves: a
    # usage limit whose reset time has passed, and a stranded stage nothing ever
    # judged. After the settlers, so a stage stranded in this very pass is
    # resumed in it; the stranded one is charged to the transient-retry budget.
    SweepStep("resume_usage_limit_waits", resume_usage_limit_waits),
    SweepStep("resume_stranded_stages", resume_stranded_stages),
    # Report-only, like the monitor scan: it previews each live tree's refresh
    # from the base and files (or closes) an inbox card. Nothing else watches
    # the base move, so without it a conflict surfaced only when a stage or a
    # chat turn next tripped on it.
    SweepStep("check_integration_branches", check_integration_branches),
    # A publish card closed only when a later publish succeeded, so one whose PR
    # a person merged by hand stayed open in a tree with nothing left to land.
    _invariant(PUBLISH_CARDS_CLOSE),
    # A survey card closed only by a click, so "clone failed" stayed pending for
    # days after the next pass cloned without trouble. The feeder now closes
    # them as it succeeds; this catches any it did not.
    _invariant(SURVEY_CARDS_CLOSE),
    # Report-only: work left in a finished ticket's tree is a person's call to
    # commit or discard. Reads `git status` of the trees `worktree_lifecycle`
    # kept, which is a handful, not one per ticket.
    _invariant(LANDED_WORK_IS_COMMITTED),
    # A branch that merged a squash of its integration branch conflicts on every
    # later checkout merge (lg-external-harness-1318). The next checkout repairs
    # it too; this catches the idle ones and reports one entering after #654.
    _invariant(TICKET_BRANCHES_HOLD_HISTORY),
    # A probe once wrote three workspaces on temp-directory repos into the live
    # database (lg-milestone-that-1319); every sweep over workspaces met them.
    _invariant(LIVE_WORKSPACES_HAVE_REAL_REPOS),
    # Closing a ticket never touched its stages, so 361 of 466 closed tickets
    # still showed pending or blocked ones (lg-workflow-integrity-1328). The
    # flush guard closes them now; this repairs the backlog and reports a writer
    # that gets past it.
    _invariant(CLOSED_TICKETS_HAVE_CLOSED_WORKFLOWS),
    # After closed tickets close: a stage awaiting a person with no approval
    # waits on nobody (lg-workflow-integrity-1625). It is blocked and settled,
    # so the operator sees why and a requeue runs it.
    _invariant(AWAITING_STAGES_ARE_ASKED),
    # A ticket held on prerequisites, or parked on them by a person, waits for
    # them to land, and nothing else reads a landing (lg-milestone-that-1315).
    # The mark comes first, so a park seen waiting in this pass is releasable in
    # a later one; last overall, so every settler and landing above has run.
    SweepStep("mark_parked_waits", mark_parked_waits),
    _invariant(HELD_TICKETS_RESUME),
    # After holds resume: dropping a parent-edge must not unpark a ticket whose
    # only remaining wait was the illegal one (release requires an edge).
    _invariant(NO_TICKET_DEPENDS_ON_AN_ANCESTOR),
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
