from loregarden.models.domain import (
    AgentRun,
    OrchestrationDriver,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
)
from loregarden.services.builtin_orchestrator import BuiltinOrchestrator
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.run_service import (
    INTERRUPTED_RUN_MESSAGE,
    RunService,
    fail_interrupted_orchestration_runs,
    fail_interrupted_runs,
)
from loregarden.services.seed import seed_database
from loregarden.services.triage_run_service import (
    INTERRUPTED_TURN_MESSAGE,
    fail_interrupted_triage_turns,
    start_triage_run,
)
from loregarden.services.triage_service import list_triage_messages
from sqlmodel import Session, select


def test_fail_interrupted_runs_marks_orphans_failed(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        orch = OrchestrationService(session)
        stuck = orch.start_run(ticket, stage_key="testing")
        stuck.status = RunStatus.RUNNING
        session.add(stuck)
        session.commit()

        failed = fail_interrupted_runs(session, ticket_id=ticket.id, stage_key="testing")
        assert len(failed) == 1
        assert failed[0].id == stuck.id
        session.refresh(stuck)
        assert stuck.status == RunStatus.FAILED
        assert INTERRUPTED_RUN_MESSAGE in stuck.stderr
        session.refresh(ticket)
        assert ticket.workflow_stage_status == StageStatus.BLOCKED


def test_interrupted_triage_turn_replies_instead_of_blocking_the_ticket(isolated_db):
    """A triage chat turn killed by a reload must answer in the chat, not block the ticket.

    The turn shares the ``triage`` stage_key with the workflow stage, so sweeping it
    through complete_run marked the stage BLOCKED off a chat message and left the
    operator's message silently unanswered.
    """
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        stage_before = ticket.workflow_stage_status

        user_message, run = start_triage_run(session, ticket, "what is the state here?")
        run.status = RunStatus.RUNNING
        session.add(run)
        session.commit()

        # The orchestration sweep must not touch it...
        assert fail_interrupted_runs(session) == []

        # ...the triage reconciliation settles it and replies.
        settled = fail_interrupted_triage_turns(session)
        assert len(settled) == 1
        assert settled[0].id == run.id

        session.refresh(run)
        assert run.status == RunStatus.FAILED

        replies = [
            m
            for m in list_triage_messages(session, ticket.id)
            if m.role == "assistant" and m.run_id == run.id
        ]
        assert len(replies) == 1
        assert INTERRUPTED_TURN_MESSAGE in replies[0].content

        session.refresh(ticket)
        assert ticket.workflow_stage_status == stage_before
        assert ticket.blocking_issues == ""
        assert user_message.role == "user"


def test_start_run_async_fails_prior_running_run(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        run_svc = RunService(session)
        first = run_svc.start_run_async(ticket, stage_key="testing")
        assert first.status == RunStatus.RUNNING

        second = run_svc.start_run_async(ticket, stage_key="testing")
        session.refresh(first)
        assert first.status == RunStatus.FAILED
        assert second.status == RunStatus.RUNNING
        assert second.id != first.id

        runs = session.exec(
            select(AgentRun).where(
                AgentRun.ticket_id == ticket.id,
                AgentRun.stage_key == "testing",
            )
        ).all()
        running = [run for run in runs if run.status == RunStatus.RUNNING]
        assert len(running) == 1
        assert running[0].id == second.id


def test_fail_interrupted_orchestration_runs_marks_orphans_failed(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        stuck = OrchestrationCallbackService(session).start_orchestration_run(
            ticket,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            profile_slug="default",
        )
        assert stuck.status == OrchestrationRunStatus.RUNNING

        failed = fail_interrupted_orchestration_runs(session, ticket_id=ticket.id)
        assert len(failed) == 1
        assert failed[0].id == stuck.id
        session.refresh(stuck)
        assert stuck.status == OrchestrationRunStatus.FAILED
        assert stuck.finished_at is not None
        assert INTERRUPTED_RUN_MESSAGE in stuck.error_message


def test_orchestrate_ticket_recovers_after_orphaned_orchestration_run(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        callbacks = OrchestrationCallbackService(session)
        stuck = callbacks.start_orchestration_run(
            ticket,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            profile_slug="default",
        )
        assert callbacks.get_active_orchestration_run(ticket.id) is not None

        fail_interrupted_orchestration_runs(session, ticket_id=ticket.id)

        assert callbacks.get_active_orchestration_run(ticket.id) is None
        session.refresh(stuck)
        assert stuck.status == OrchestrationRunStatus.FAILED


def test_recover_interrupted_stage_clears_stale_block(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        orch = OrchestrationService(session)
        stuck = orch.start_run(ticket, stage_key="testing")
        stuck.status = RunStatus.RUNNING
        session.add(stuck)
        session.commit()
        fail_interrupted_runs(session, ticket_id=ticket.id, stage_key="testing")
        session.refresh(ticket)
        assert ticket.workflow_stage_status == StageStatus.BLOCKED
        assert ticket.blocking_issues == INTERRUPTED_RUN_MESSAGE

        builtin = BuiltinOrchestrator(session)
        instance, stages = builtin.orch._resolve_stages(ticket)
        recovered = builtin._recover_interrupted_stage(ticket, instance, stages)
        assert recovered == "testing"

        session.refresh(ticket)
        assert ticket.workflow_stage_status == StageStatus.PENDING
        assert ticket.blocking_issues == ""


def test_recover_interrupted_stage_ignores_genuine_block(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = session.exec(
            select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
        ).first()
        callbacks = OrchestrationCallbackService(session)
        orch_run = callbacks.start_orchestration_run(
            ticket, driver=OrchestrationDriver.BUILTIN_AUTOPILOT, profile_slug="default"
        )
        callbacks.block_ticket(
            orch_run, ticket, stage_key="testing", message="Real test failure: assertion error"
        )
        session.refresh(ticket)
        assert ticket.workflow_stage_status == StageStatus.BLOCKED

        builtin = BuiltinOrchestrator(session)
        instance, stages = builtin.orch._resolve_stages(ticket)
        recovered = builtin._recover_interrupted_stage(ticket, instance, stages)
        assert recovered is None

        session.refresh(ticket)
        assert ticket.workflow_stage_status == StageStatus.BLOCKED
        assert "Real test failure" in ticket.blocking_issues
