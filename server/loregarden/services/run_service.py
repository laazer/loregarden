from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timedelta, timezone

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.db.session import engine
from loregarden.models.domain import (
    AgentRun,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    Workspace,
)
from loregarden.services.artifact_service import record_blocking_issue
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.hierarchy_service import collect_ticket_scope_ids
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.run_interruption import (
    INTERRUPTED_RUN_MESSAGE,
    STRANDED_STAGE_MESSAGE,
)
from loregarden.services.scheduling import set_orchestration_scheduler
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import set_stage_status
from sqlalchemy import func
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)

TERMINAL_HANDOFF_COMMAND_PREFIX = "[terminal-handoff]"

STALE_HANDOFF_NEVER_STARTED_MESSAGE = (
    "Terminal handoff was never started — the copied command did not check in. "
    "Generate a fresh handoff or re-run the stage to continue."
)

STALE_HANDOFF_SHELL_DIED_MESSAGE = (
    "Terminal handoff shell exited without completing the stage. Re-run the stage to continue."
)

HANDOFF_EXITED_MESSAGE = (
    "Terminal handoff CLI session ended without completing the stage. Re-run the stage to continue."
)


def _handoff_checkin_grace_seconds() -> int:
    raw = os.environ.get("LOREGARDEN_HANDOFF_CHECKIN_GRACE_SECONDS", "")
    return int(raw) if raw.isdigit() else 900


def _pid_alive(pid: int) -> bool:
    """Whether `pid` is a live process on this host.

    Valid only because terminal handoffs are pasted into a shell on the same
    machine as this control plane — there is no remote-execution path.
    """
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def settle_dead_handoff_run(
    session: Session, run: AgentRun, *, message: str, session_ran: bool = True
) -> None:
    """Settle a handoff run whose terminal session is gone.

    Nothing settles a handoff AgentRun while its terminal works — the session
    advances the *workflow* through MCP/UI and the run row just sits RUNNING. So
    the workflow's own state is the only record of what the session achieved:

    - stage still RUNNING → the dead session was the active work; fail the run
      and let the advance mark the stage BLOCKED for a re-run.
    - stage progressed (moved to another key, DONE, or AWAITING a gate) and a
      session actually ran (``session_ran``) → it did its job before ending; the
      run succeeded, and re-touching the workflow here would corrupt real state
      (e.g. re-BLOCK an AWAITING gate). A never-started handoff earns no such
      credit — a human advancing the stage by hand does not make the run a
      success — but the workflow is still left alone.
    - anything else (already BLOCKED, ticket gone) → fail the run, leave the
      workflow alone.
    """
    ticket = session.get(Ticket, run.ticket_id)
    stage_still_running = bool(
        ticket
        and ticket.workflow_stage_key == run.stage_key
        and ticket.workflow_stage_status == StageStatus.RUNNING
    )
    progressed = session_ran and bool(
        ticket
        and (
            ticket.workflow_stage_key != run.stage_key
            or ticket.workflow_stage_status in (StageStatus.DONE, StageStatus.AWAITING)
        )
    )
    OrchestrationService(session).complete_run(
        run,
        status=RunStatus.SUCCEEDED if progressed else RunStatus.FAILED,
        stderr="" if progressed else message,
        advance_workflow=stage_still_running,
    )


def fail_stale_handoff_runs(session: Session, *, ticket_id: str | None = None) -> list[AgentRun]:
    """Reap terminal-handoff runs that are provably dead.

    A handoff run is created RUNNING before any process exists (see
    ``prepare_terminal_handoff``), so an in-flight status alone proves nothing.
    Two conditions are decisive: the command never checked in within the grace
    period (it was never pasted), or it checked in with a shell pid that is no
    longer alive (the terminal died). Anything else is treated as live.
    """
    query = select(AgentRun).where(
        col(AgentRun.status).in_([RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]),
        col(AgentRun.command).startswith(TERMINAL_HANDOFF_COMMAND_PREFIX),
    )
    if ticket_id:
        query = query.where(AgentRun.ticket_id == ticket_id)

    now = datetime.now(timezone.utc)
    grace = _handoff_checkin_grace_seconds()
    reaped: list[AgentRun] = []
    for run in session.exec(query).all():
        if run.handoff_accepted_at is None:
            started_at = run.started_at or run.created_at
            if started_at.tzinfo is None:
                started_at = started_at.replace(tzinfo=timezone.utc)
            if (now - started_at).total_seconds() < grace:
                continue
            settle_dead_handoff_run(
                session, run, message=STALE_HANDOFF_NEVER_STARTED_MESSAGE, session_ran=False
            )
        elif run.handoff_pid is not None and not _pid_alive(run.handoff_pid):
            settle_dead_handoff_run(session, run, message=STALE_HANDOFF_SHELL_DIED_MESSAGE)
        else:
            continue
        reaped.append(run)
    return reaped


def fail_interrupted_runs(
    session: Session,
    *,
    ticket_id: str | None = None,
    stage_key: str | None = None,
    exclude_run_id: str | None = None,
    message: str = INTERRUPTED_RUN_MESSAGE,
) -> list[AgentRun]:
    """Mark orphaned in-flight runs as failed so stages do not stay stuck running.

    Skips triage chat turns. They share the ``triage`` stage_key with the workflow
    stage but are a side channel: routing one through ``complete_run`` would advance
    the workflow off a chat message — blocking the ticket. ``agent_id`` is the
    discriminator (see ``triage_service.triage_run_status``); they are reconciled by
    ``triage_run_service.fail_interrupted_triage_turns`` instead.
    """
    query = select(AgentRun).where(
        col(AgentRun.status).in_([RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]),
        AgentRun.agent_id != TRIAGE_AGENT_ID,
    )
    if ticket_id:
        query = query.where(AgentRun.ticket_id == ticket_id)
    else:
        # Same reasoning as fail_interrupted_orchestration_runs: a stage checked
        # out to an outside harness is not orphaned by this process restarting.
        # A ticket-scoped reap still claims it — that call is a deliberate
        # "start this stage again", which does supersede whoever held it.
        query = query.where(col(AgentRun.external_harness).is_(None))
    if stage_key:
        query = query.where(AgentRun.stage_key == stage_key)
    if exclude_run_id:
        query = query.where(AgentRun.id != exclude_run_id)

    orch = OrchestrationService(session)
    failed: list[AgentRun] = []
    for run in session.exec(query).all():
        orch.complete_run(run, status=RunStatus.FAILED, stderr=message)
        failed.append(run)
    return failed


def settle_stranded_stages(
    session: Session,
    *,
    ticket_id: str | None = None,
    message: str = STRANDED_STAGE_MESSAGE,
) -> list[Ticket]:
    """Settle stages left RUNNING with no live run behind them.

    ``fail_interrupted_runs`` reaps by *run*: it selects runs still in flight and
    completes them, which settles their stage on the way through. A stage whose run
    already reached a terminal status without settling it is therefore invisible to
    it — and permanently so, since no later reap will ever select that run again.

    That state is reachable: ``complete_run`` commits the run's terminal status
    before it touches the ticket, so any failure in between leaves exactly this
    residue. It is not cosmetic — a ticket stuck at RUNNING reports itself as an
    active workflow stage, which deadlocks the self-improve restart watcher, so the
    reload that would have recovered it can never fire.

    QUEUED counts as live: a stage legitimately reads RUNNING while its run waits in
    the queue.
    """
    live_ticket_ids = select(AgentRun.ticket_id).where(
        col(AgentRun.status).in_(
            [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]
        )
    )
    query = select(Ticket).where(
        Ticket.workflow_stage_status == StageStatus.RUNNING,
        col(Ticket.id).not_in(live_ticket_ids),
    )
    if ticket_id:
        query = query.where(Ticket.id == ticket_id)

    orch = OrchestrationService(session)
    settled: list[Ticket] = []
    for ticket in session.exec(query).all():
        instance, _ = orch.ensure_workflow_instance(ticket, commit=False)
        _, stages = resolve_ticket_stages(session, ticket)
        stage_key = ticket.workflow_stage_key
        if not instance or not stages or not stage_key:
            continue
        try:
            set_stage_status(ticket, instance, stages, stage_key, StageStatus.BLOCKED)
        except ValueError:
            # Stage key no longer in the template — nothing coherent to settle.
            continue
        ticket.blocking_issues = record_blocking_issue(
            session,
            ticket,
            run_id=None,
            stage_key=stage_key,
            message=message,
        )
        session.add(ticket)
        session.add(instance)
        settled.append(ticket)
    if settled:
        session.commit()
        logger.warning(
            "Settled %d stage(s) left running with no live run: %s",
            len(settled),
            ", ".join(t.external_id for t in settled),
        )
    return settled


def fail_interrupted_orchestration_runs(
    session: Session,
    *,
    ticket_id: str | None = None,
    message: str = INTERRUPTED_RUN_MESSAGE,
) -> list[OrchestrationRun]:
    """Mark orphaned orchestration runs as failed.

    An OrchestrationRun left at RUNNING after a server reload/crash makes
    start_orchestration_run() refuse all future runs for its ticket ("Orchestration
    already running"), even though nothing is actually running. fail_interrupted_runs
    already fails the orphaned AgentRun beneath it; this does the same for the parent.
    """
    query = select(OrchestrationRun).where(
        OrchestrationRun.status == OrchestrationRunStatus.RUNNING,
        # An external-harness run has no process here to be orphaned by a reload:
        # it lives in someone's own terminal, which is the reason to use one.
        # Failing it on startup would end the run mid-ticket every time this
        # server restarted. Abandoned ones are cancelled by the operator.
        col(OrchestrationRun.external_harness).is_(None),
    )
    if ticket_id:
        query = query.where(OrchestrationRun.ticket_id == ticket_id)

    callbacks = OrchestrationCallbackService(session)
    failed: list[OrchestrationRun] = []
    for run in session.exec(query).all():
        ticket = session.get(Ticket, run.ticket_id)
        if not ticket:
            continue
        callbacks.complete_orchestration(
            run, ticket, status=OrchestrationRunStatus.FAILED, message=message
        )
        failed.append(run)
    return failed


#: How long a ticket tree may show no live agent run and no new activity before
#: an orchestration over it is treated as stalled. Generous on purpose: the
#: only thing this window has to cover is the gap *between* stages, since a
#: running agent keeps its own row live for as long as it works.
STALLED_ORCHESTRATION_GRACE = timedelta(minutes=15)

STALLED_ORCHESTRATION_MESSAGE = (
    "Orchestration stalled: nothing in this ticket tree has a live agent run and "
    "nothing has progressed recently. Its driver is gone; the lane has been released."
)


def _tree_ticket_ids(session: Session, ticket_id: str) -> list[str]:
    """Every ticket in the tree this one belongs to — root included.

    The *whole* tree, not the subtree below this ticket, and that is the point.
    A parent orchestration runs no stages of its own: the work lives in its
    children's separate orchestration runs, so a check scoped to one run's own
    ticket would read an actively working parent as idle and kill the tree from
    the top. Walking up first and then down makes any live agent anywhere in the
    tree protect every orchestration in it.
    """
    root_id = ticket_id
    seen: set[str] = {ticket_id}
    while True:
        parent_id = session.exec(
            select(Ticket.parent_ticket_id).where(Ticket.id == root_id)
        ).first()
        if not parent_id or parent_id in seen:
            break
        seen.add(parent_id)
        root_id = parent_id
    return collect_ticket_scope_ids(session, root_id)


def _tree_last_activity(session: Session, ticket_ids: list[str]) -> datetime | None:
    """The most recent sign of life anywhere in a ticket tree."""
    stamps = [
        session.exec(select(func.max(column)).where(col(owner).in_(ticket_ids))).first()
        for column, owner in (
            (AgentRun.created_at, AgentRun.ticket_id),
            (AgentRun.finished_at, AgentRun.ticket_id),
            # `created_at`, not `started_at`: a claim is written before anything
            # runs and leaves `started_at` null until a driver adopts it, so
            # reading that alone dates every fresh claim to the beginning of
            # time and settles it on the very next status read.
            (OrchestrationRun.created_at, OrchestrationRun.ticket_id),
            (OrchestrationRun.started_at, OrchestrationRun.ticket_id),
        )
    ]
    # SQLite hands these back naive; comparing one to an aware `cutoff` raises.
    aware = [
        stamp if stamp.tzinfo else stamp.replace(tzinfo=timezone.utc) for stamp in stamps if stamp
    ]
    return max(aware) if aware else None


def settle_stalled_orchestrations(
    session: Session,
    *,
    grace: timedelta = STALLED_ORCHESTRATION_GRACE,
    message: str = STALLED_ORCHESTRATION_MESSAGE,
) -> list[OrchestrationRun]:
    """Fail orchestrations whose driver is gone, so their lane comes back.

    `fail_interrupted_orchestration_runs` is the sibling of this, and it only
    ever runs at startup — it reaps what a restart orphaned. Nothing reaped an
    orchestration whose thread died *while the server stayed up*, and nothing
    could: `reconcile_slots` frees a slot whose occupant is terminal, and a run
    stuck at RUNNING is live by that test. So the lane was held until the next
    boot, the board reported it busy, and whatever queued behind it never
    started. Two of three lanes were in that state when this was written.

    Liveness is judged over the whole ticket tree (see `_tree_ticket_ids`) and
    needs both halves to be false: no agent run in flight anywhere in the tree,
    *and* no new activity within `grace`. A run that is genuinely working keeps
    an AgentRun row live for its whole duration, however long that is, so the
    first condition alone protects it — a 51-minute agent run was in flight
    while this was being written and must survive untouched.
    """
    live_runs = select(AgentRun.ticket_id).where(
        col(AgentRun.status).in_(
            [RunStatus.QUEUED, RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION]
        )
    )
    busy_ticket_ids = {row for row in session.exec(live_runs).all() if row}

    candidates = session.exec(
        select(OrchestrationRun).where(
            col(OrchestrationRun.status).in_(
                [OrchestrationRunStatus.QUEUED, OrchestrationRunStatus.RUNNING]
            )
        )
    ).all()

    callbacks = OrchestrationCallbackService(session)
    cutoff = datetime.now(timezone.utc) - grace
    settled: list[OrchestrationRun] = []
    for run in candidates:
        ticket = session.get(Ticket, run.ticket_id)
        if not ticket:
            continue
        tree = _tree_ticket_ids(session, ticket.id)
        if busy_ticket_ids.intersection(tree):
            continue
        last_activity = _tree_last_activity(session, tree)
        if last_activity is None or last_activity > cutoff:
            # No timestamp anywhere in the tree is not evidence of a stall, it
            # is an absence of evidence — and the cost of being wrong here is
            # killing live work, so it spares.
            continue

        logger.warning(
            "Settling stalled orchestration %s (ticket %s): no live agent run in its "
            "tree and no activity since %s",
            run.run_code,
            ticket.external_id or ticket.id,
            last_activity,
        )
        callbacks.complete_orchestration(
            run, ticket, status=OrchestrationRunStatus.FAILED, message=message
        )
        settled.append(run)
    return settled


def execute_agent_run_background(run_id: str) -> None:
    """Run agent CLI with a fresh DB session."""
    try:
        with Session(engine) as session:
            run_svc = RunService(session)
            run = run_svc.get_run(run_id)
            if not run:
                logger.error("Background run not found: %s", run_id)
                return
            ticket = session.get(Ticket, run.ticket_id)
            if not ticket:
                logger.error("Background run ticket not found: %s", run_id)
                return
            run_svc.executor.execute(run, ticket)
    except Exception as exc:
        logger.exception("Background agent run failed: %s", run_id)
        try:
            with Session(engine) as session:
                run = session.get(AgentRun, run_id)
                if run and run.status in {RunStatus.RUNNING, RunStatus.AWAITING_PERMISSION}:
                    OrchestrationService(session).complete_run(
                        run,
                        status=RunStatus.FAILED,
                        stderr=str(exc)[:2000] or "Background agent run failed",
                    )
        except Exception:
            logger.exception("Failed to mark run %s as failed after background error", run_id)


def schedule_agent_run(run_id: str) -> None:
    """Queue CLI execution without blocking the API event loop."""
    if os.environ.get("LOREGARDEN_SYNC_RUNS") == "1":
        execute_agent_run_background(run_id)
        return
    thread = threading.Thread(
        target=execute_agent_run_background,
        args=(run_id,),
        name=f"loregarden-run-{run_id[:8]}",
        daemon=True,
    )
    thread.start()


def execute_orchestration_background(
    ticket_id: str,
    *,
    max_stages: int | None = None,
    driver=None,
    stop_at_stage_key: str | None = None,
    auto_approve: bool = False,
    timeout_seconds: int | None = None,
) -> None:
    try:
        with Session(engine) as session:
            ticket = session.get(Ticket, ticket_id)
            if not ticket:
                logger.error("Background orchestration ticket not found: %s", ticket_id)
                return
            RunService(session).orchestrate_ticket(
                ticket,
                max_stages=max_stages,
                driver=driver,
                stop_at_stage_key=stop_at_stage_key,
                auto_approve=auto_approve,
                timeout_seconds=timeout_seconds,
            )
    except Exception as exc:
        logger.exception("Background orchestration failed for ticket %s: %s", ticket_id, exc)


def schedule_orchestration(
    ticket_id: str,
    *,
    max_stages: int | None = None,
    driver=None,
    stop_at_stage_key: str | None = None,
    auto_approve: bool = False,
    timeout_seconds: int | None = None,
) -> None:
    """Queue orchestration without blocking the API event loop."""
    if os.environ.get("LOREGARDEN_SYNC_ORCHESTRATION") == "1":
        execute_orchestration_background(
            ticket_id,
            max_stages=max_stages,
            driver=driver,
            stop_at_stage_key=stop_at_stage_key,
            auto_approve=auto_approve,
            timeout_seconds=timeout_seconds,
        )
        return
    thread = threading.Thread(
        target=execute_orchestration_background,
        args=(ticket_id,),
        kwargs={
            "max_stages": max_stages,
            "driver": driver,
            "stop_at_stage_key": stop_at_stage_key,
            "auto_approve": auto_approve,
            "timeout_seconds": timeout_seconds,
        },
        name=f"loregarden-orch-{ticket_id[:8]}",
        daemon=True,
    )
    thread.start()


class RunService:
    def __init__(self, session: Session) -> None:
        self.session = session
        self.orchestration = OrchestrationService(session)
        self.executor = CliAgentExecutor(session)

    def orchestrate_ticket(
        self,
        ticket: Ticket,
        *,
        driver=None,
        max_stages: int | None = None,
        stop_at_stage_key: str | None = None,
        auto_approve: bool = False,
        timeout_seconds: int | None = None,
    ) -> OrchestrationRun:
        ws = self.session.get(Workspace, ticket.workspace_id)
        if not ws:
            raise ValueError("Ticket workspace not found")
        profile = resolve_orchestration_profile(ws)
        chosen = driver or profile.driver

        if chosen == OrchestrationDriver.BUILTIN_AUTOPILOT:
            return BuiltinOrchestrator(self.session).execute(
                ticket,
                profile,
                max_stages=max_stages,
                stop_at_stage_key=stop_at_stage_key,
                auto_approve=auto_approve,
                timeout_seconds=timeout_seconds,
            )
        if chosen == OrchestrationDriver.EXTERNAL_MCP:
            return OrchestrationCallbackService(self.session).start_orchestration_run(
                ticket,
                driver=chosen,
                profile_slug=profile.slug,
                auto_approve=auto_approve,
                stop_at_stage_key=stop_at_stage_key or "",
                timeout_override_seconds=timeout_seconds,
            )
        raise ValueError("manual_stage driver uses POST /start with manual=true")

    def start_and_execute(
        self, ticket: Ticket, *, stage_key: str | None = None
    ) -> tuple[AgentRun, Ticket]:
        run = self.orchestration.start_run(ticket, stage_key=stage_key)
        self.session.refresh(ticket)
        completed_run = self.executor.execute(run, ticket)
        self.session.refresh(ticket)
        return completed_run, ticket

    def start_run_async(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        auto_approve: bool = False,
        timeout_seconds: int | None = None,
    ) -> AgentRun:
        """Create a run and mark the stage running; CLI executes in a background task."""
        target_key = stage_key or ticket.workflow_stage_key
        fail_interrupted_runs(
            self.session,
            ticket_id=ticket.id,
            stage_key=target_key or None,
        )
        run = self.orchestration.start_run(
            ticket,
            stage_key=stage_key,
            auto_approve=auto_approve,
            timeout_override_seconds=timeout_seconds,
        )
        self.session.refresh(ticket)
        return run

    def start_stage_execution(
        self,
        ticket: Ticket,
        *,
        stage_key: str | None = None,
        auto_approve: bool = False,
        timeout_seconds: int | None = None,
    ) -> AgentRun | None:
        """Start an agent CLI run, or enter a human approval gate for agentless stages."""
        from loregarden.services.studio_routing import is_agentless_stage

        template = self.orchestration.get_template_for_ticket(ticket)
        if not template:
            raise ValueError("No workflow template for ticket workspace")

        _, stages = self.orchestration._resolve_stages(ticket)
        if not stages:
            raise ValueError("Ticket has no workflow instance")

        target_key = stage_key or ticket.workflow_stage_key
        if not target_key:
            raise ValueError("No stage selected")

        stage_def = next((s for s in stages if s.key == target_key), None)
        if not stage_def:
            raise ValueError(f"Unknown stage key: {target_key}")

        if is_agentless_stage(stage_def):
            if stage_def.key == "done":
                self.orchestration.finalize_workflow(ticket)
                self.session.refresh(ticket)
                return None
            self.orchestration.enter_human_gate(ticket, stage_key=target_key)
            self.session.refresh(ticket)
            return None

        return self.start_run_async(
            ticket, stage_key=stage_key, auto_approve=auto_approve, timeout_seconds=timeout_seconds
        )

    def list_runs(
        self,
        *,
        ticket_id: str | None = None,
        limit: int = 50,
        include_triage: bool = False,
    ) -> list[AgentRun]:
        query = select(AgentRun).order_by(AgentRun.created_at.desc()).limit(limit)
        if ticket_id:
            query = query.where(AgentRun.ticket_id == ticket_id)
        if not include_triage:
            query = query.where(AgentRun.agent_id != TRIAGE_AGENT_ID)
        return list(self.session.exec(query).all())

    def get_run(self, run_id: str) -> AgentRun | None:
        return self.session.get(AgentRun, run_id)


def _scheduled_orchestration(ticket_id: str, **kwargs) -> None:
    """Adapter installed into the `scheduling` seam.

    A wrapper rather than the function itself, so the name is resolved in this
    module's globals at *call* time. Handing the seam the function object froze
    it at import: patching `run_service.schedule_orchestration` — which the
    approval-resume tests do — rebound the module attribute while the seam went
    on calling the original.
    """
    schedule_orchestration(ticket_id, **kwargs)


# Installed here so lower modules can start a pipeline without importing this
# one, which imports the builtin driver and therefore most of the orchestrator.
set_orchestration_scheduler(_scheduled_orchestration)
