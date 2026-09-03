from __future__ import annotations

import logging
import os
import threading
from datetime import datetime, timezone

from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.db.session import engine
from loregarden.models.domain import (
    AgentRun,
    DispatchSurface,
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
from loregarden.services.drain import DRAIN_REFUSED_REASON, is_draining
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import resolve_orchestration_profile
from loregarden.services.run_concurrency import orchestration_lease_expired
from loregarden.services.run_interruption import (
    INTERRUPTED_RUN_MESSAGE,
    ORPHAN_OF_TERMINAL_ORCH_MESSAGE,
    STRANDED_STAGE_MESSAGE,
)
from loregarden.services.run_lease import (
    AGENT_RUN_LEASE,
    SUPERVISED,
    agent_run_lease_expired,
    lease_renewal,
    pid_alive,
)
from loregarden.services.run_reattach import surviving_runs
from loregarden.services.scheduling import set_orchestration_scheduler
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from loregarden.services.workflow_service import resolve_ticket_stages
from loregarden.services.workflow_state import set_stage_status
from sqlalchemy.orm import defer
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
        elif run.handoff_pid is not None and not pid_alive(run.handoff_pid):
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
        # 470. Same side of the same distinction: since 317 detaches the agent,
        # a run sitting at RUNNING during startup may have a live process behind
        # it rather than a dead one. Failing it would be the reaper killing the
        # row of a working agent — strictly worse than the restart it replaced.
        # A ticket-scoped call still claims it, for the reason above.
        survivors = {run.id for run in surviving_runs(session)}
        if survivors:
            query = query.where(col(AgentRun.id).not_in(survivors))
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
        OrchestrationRun.status == OrchestrationRunStatus.RUNNING
    )
    if ticket_id:
        query = query.where(OrchestrationRun.ticket_id == ticket_id)

    callbacks = OrchestrationCallbackService(session)
    failed: list[OrchestrationRun] = []
    for run in session.exec(query).all():
        # An external-harness run has no process here to be orphaned by a
        # reload: it lives in someone's own terminal, which is the reason to use
        # one. Failing it on startup would end the run mid-ticket every time
        # this server restarted, so it is exempt — but only while its lease
        # holds. The exemption used to be unconditional and the cleanup was
        # delegated to an operator surface that does not exist, which is how an
        # abandoned session came to hold a lane permanently rather than until
        # restart. "Not auto-resumed" and "never settled" were being conflated;
        # only the first was ever decided.
        if run.external_harness and not orchestration_lease_expired(session, run):
            continue
        ticket = session.get(Ticket, run.ticket_id)
        if not ticket:
            continue
        callbacks.complete_orchestration(
            run, ticket, status=OrchestrationRunStatus.FAILED, message=message
        )
        failed.append(run)
    return failed


EXPIRED_LEASE_MESSAGE = (
    "Orchestration lease expired: nothing has renewed this run and no agent run is in "
    "flight beneath it. Its driver is gone; the lane has been released."
)


def settle_expired_orchestration_leases(
    session: Session, *, message: str = EXPIRED_LEASE_MESSAGE
) -> list[OrchestrationRun]:
    """Make a run whose lease expired terminal, without waiting for a restart.

    The lease alone frees the *slot* — `_occupant_is_live` consults it on every
    status read — but it does not settle the *run*, and only
    `fail_interrupted_orchestration_runs` does that, from the startup lifespan.
    A run left RUNNING with its lane already returned is worse than untidy:
    `claim_orchestration_run` adopts any active run for a ticket, so that ticket
    could not be orchestrated again until a reboot, and `ticket_activity` counts
    a RUNNING orchestration as running, so the ticket kept reporting an agent on
    it — the original symptom, arriving by a second route.

    Runs on the same cadence as the lane reconcile and for the same reason:
    there is no periodic tick in this server, and the board is where a lane busy
    with nothing gets noticed.
    """
    candidates = session.exec(
        select(OrchestrationRun).where(
            col(OrchestrationRun.status).in_(
                [OrchestrationRunStatus.QUEUED, OrchestrationRunStatus.RUNNING]
            )
        )
    ).all()

    callbacks = OrchestrationCallbackService(session)
    settled: list[OrchestrationRun] = []
    for run in candidates:
        if not orchestration_lease_expired(session, run):
            continue
        ticket = session.get(Ticket, run.ticket_id)
        if not ticket:
            continue
        logger.warning(
            "Settling orchestration %s (ticket %s): lease expired, last seen %s",
            run.run_code,
            ticket.external_id or ticket.id,
            run.last_seen_at or run.started_at or run.created_at,
        )
        callbacks.complete_orchestration(
            run, ticket, status=OrchestrationRunStatus.FAILED, message=message
        )
        settled.append(run)
    return settled


AGENT_LEASE_EXPIRED_MESSAGE = (
    "Agent run lease expired: nothing has renewed this run, so the thread that was "
    "supervising it is gone. Failed by the reconciliation sweep rather than by a restart."
)


def settle_expired_agent_runs(
    session: Session, *, message: str = AGENT_LEASE_EXPIRED_MESSAGE
) -> list[AgentRun]:
    """Fail in-flight runs whose lease says nobody is supervising them.

    The predicate-based counterpart to `fail_interrupted_runs`, and deliberately
    a separate function rather than a flag on it. That one carries restart
    semantics — every in-flight row is an orphan of the process that just died —
    and is correct exactly once, at boot. This one tests each run and is safe to
    call while other runs are genuinely live, which is what lets it run on a
    clock.

    Fails closed twice over: `agent_run_lease_expired` spares any run kind with
    no defined renewer, and this spares anything it cannot resolve a ticket for.
    """
    candidates = session.exec(
        select(AgentRun).where(col(AgentRun.status).in_(list(SUPERVISED)))
    ).all()

    settled: list[AgentRun] = []
    for run in candidates:
        if not agent_run_lease_expired(session, run):
            continue
        stamp = run.last_seen_at or run.started_at or run.created_at
        logger.warning(
            "Settling agent run %s (ticket %s): lease expired, last seen %s",
            run.run_code,
            run.ticket_id,
            stamp,
        )
        # 625 AC3. The run row is where this gets diagnosed months later, and a
        # bare verdict there cost three wrong diagnoses: the message said which
        # sweep had spoken but not what it judged, so the only way to check it
        # was to reconstruct elapsed time from two timestamps and guess at the
        # lease. Recording the inputs makes the row self-contained — and would
        # have shown immediately whether the run really looked unrenewed.
        OrchestrationService(session).complete_run(
            run,
            status=RunStatus.FAILED,
            stderr=(
                f"{message}\n"
                f"  last renewed: {stamp}\n"
                f"  lease: {AGENT_RUN_LEASE}\n"
                f"  external_harness: {run.external_harness.value if run.external_harness else 'none'}\n"
                f"  handoff_pid: {run.handoff_pid if run.handoff_pid is not None else 'none'}"
            ),
        )
        settled.append(run)
    return settled


#: An orchestration still claiming a lane. Children of anything else are residue.
_LIVE_ORCHESTRATION_STATUSES = (
    OrchestrationRunStatus.QUEUED,
    OrchestrationRunStatus.RUNNING,
)


def settle_orphaned_agent_runs(
    session: Session, *, message: str = ORPHAN_OF_TERMINAL_ORCH_MESSAGE
) -> list[AgentRun]:
    """Fail in-flight agent runs whose parent orchestration is already terminal.

    External-harness children do not renew a lease, so ``settle_expired_agent_runs``
    spares them forever. When the parent has already failed, succeeded, blocked,
    or cancelled, those rows are not work in flight — they are the leftover
    ``RUNNING`` that made a finished ticket look busy on the home board.
    ``advance_workflow=False``: the orchestration already made the ticket's
    decision; failing residue must not take a second pass at the stage.
    """
    candidates = session.exec(
        select(AgentRun)
        .where(col(AgentRun.status).in_(list(SUPERVISED)))
        .where(col(AgentRun.orchestration_run_id).is_not(None))
    ).all()

    settled: list[AgentRun] = []
    for run in candidates:
        parent = session.get(OrchestrationRun, run.orchestration_run_id)
        if parent is None or parent.status in _LIVE_ORCHESTRATION_STATUSES:
            continue
        logger.warning(
            "Settling agent run %s (ticket %s): parent orchestration %s is %s",
            run.run_code,
            run.ticket_id,
            parent.run_code,
            parent.status.value,
        )
        OrchestrationService(session).complete_run(
            run, status=RunStatus.FAILED, stderr=message, advance_workflow=False
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
            with lease_renewal(run.id):
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
    """Queue CLI execution without blocking the API event loop.

    Refuses while draining: starting a turn the process is about to abandon
    wastes an agent invocation and produces a run to interrupt seconds later.
    """
    if is_draining():
        logger.info("Refusing to start run %s: %s", run_id, DRAIN_REFUSED_REASON)
        return
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
    """Queue orchestration without blocking the API event loop.

    Refuses while draining, for the same reason as `schedule_agent_run`.
    """
    if is_draining():
        logger.info("Refusing to orchestrate ticket %s: %s", ticket_id, DRAIN_REFUSED_REASON)
        return
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
        with lease_renewal(run.id):
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
        force: bool = False,
        dispatch_surface: DispatchSurface = DispatchSurface.HTTP,
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
            force=force,
            dispatch_surface=dispatch_surface,
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
        force: bool = False,
        dispatch_surface: DispatchSurface = DispatchSurface.HTTP,
    ) -> AgentRun | None:
        """Start an agent CLI run, or enter a human approval gate for agentless stages.

        ``force`` spends one dispatch past an exhausted stage retry budget — the
        deliberate human re-run the breaker is not meant to stop.
        """
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
            ticket,
            stage_key=stage_key,
            auto_approve=auto_approve,
            timeout_seconds=timeout_seconds,
            force=force,
            dispatch_surface=dispatch_surface,
        )

    def list_runs(
        self,
        *,
        ticket_id: str | None = None,
        limit: int = 50,
        include_triage: bool = False,
    ) -> list[AgentRun]:
        query = (
            select(AgentRun)
            .options(defer(AgentRun.stdout), defer(AgentRun.stderr))
            .order_by(AgentRun.created_at.desc())
            .limit(limit)
        )
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
