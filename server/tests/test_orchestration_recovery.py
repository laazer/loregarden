import asyncio
from unittest.mock import call, patch

from loregarden.main import lifespan
from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_recovery import (
    InterruptionResume,
    resume_interrupted_orchestrations,
    schedule_interrupted_resumes,
)
from loregarden.services.run_interruption import INTERRUPTED_RUN_MESSAGE
from loregarden.services.run_service import (
    fail_interrupted_orchestration_runs,
    fail_interrupted_runs,
    settle_stranded_stages,
)
from loregarden.services.seed import seed_database
from sqlmodel import Session, select


def _ticket(session: Session) -> Ticket:
    ticket = session.exec(
        select(Ticket).where(Ticket.external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket is not None
    return ticket


def _interrupt_builtin(
    session: Session,
    *,
    auto_approve: bool = False,
    stop_at_stage_key: str = "",
) -> tuple[Ticket, OrchestrationRun]:
    seed_database(session)
    ticket = _ticket(session)
    orchestration_run = OrchestrationCallbackService(session).start_orchestration_run(
        ticket,
        driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
        profile_slug="default",
        auto_approve=auto_approve,
        stop_at_stage_key=stop_at_stage_key,
    )
    agent_run = OrchestrationService(session).start_run(
        ticket,
        stage_key="testing",
        orchestration_run_id=orchestration_run.id,
    )
    agent_run.status = RunStatus.RUNNING
    session.add(agent_run)
    session.commit()

    fail_interrupted_runs(session, ticket_id=ticket.id)
    fail_interrupted_orchestration_runs(session, ticket_id=ticket.id)
    session.refresh(ticket)
    session.refresh(orchestration_run)
    return ticket, orchestration_run


def test_startup_resume_preserves_failed_rows_and_run_options(isolated_db):
    with Session(isolated_db) as session:
        ticket, old_run = _interrupt_builtin(
            session,
            auto_approve=True,
            stop_at_stage_key="script_review",
        )

        with patch(
            "loregarden.services.orchestration_recovery.schedule_interrupted_resumes"
        ) as schedule:
            resumed = resume_interrupted_orchestrations(session)

        assert resumed == [ticket.id]
        assert old_run.status == OrchestrationRunStatus.FAILED
        assert old_run.error_message == INTERRUPTED_RUN_MESSAGE
        schedule.assert_called_once_with(
            [
                InterruptionResume(
                    ticket_id=ticket.id,
                    auto_approve=True,
                    stop_at_stage_key="script_review",
                )
            ]
        )
        from loregarden.models.domain import AgentSlot

        claim = OrchestrationCallbackService(session).get_active_orchestration_run(ticket.id)
        assert claim is not None
        held = session.exec(
            select(AgentSlot).where(AgentSlot.current_orchestration_run_id == claim.id)
        ).one()
        assert held.is_available is False


def test_startup_resume_includes_a_stranded_stage(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        orchestration_run = OrchestrationCallbackService(session).start_orchestration_run(
            ticket,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            profile_slug="default",
        )
        agent_run = OrchestrationService(session).start_run(
            ticket,
            stage_key="testing",
            orchestration_run_id=orchestration_run.id,
        )
        agent_run.status = RunStatus.FAILED
        session.add(agent_run)
        session.commit()
        assert settle_stranded_stages(session, ticket_id=ticket.id) == [ticket]
        fail_interrupted_orchestration_runs(session, ticket_id=ticket.id)

        with patch(
            "loregarden.services.orchestration_recovery.schedule_interrupted_resumes"
        ) as schedule:
            assert resume_interrupted_orchestrations(session) == [ticket.id]

        schedule.assert_called_once()


def test_startup_resume_ignores_genuine_terminal_and_external_blocks(isolated_db):
    with Session(isolated_db) as session:
        ticket, _ = _interrupt_builtin(session)

        ticket.blocking_issues = "Real test failure: assertion error"
        session.add(ticket)
        session.commit()
        with patch(
            "loregarden.services.orchestration_recovery.schedule_interrupted_resumes"
        ) as schedule:
            assert resume_interrupted_orchestrations(session) == []
            schedule.assert_called_once_with([])

        ticket.blocking_issues = INTERRUPTED_RUN_MESSAGE
        ticket.state = TicketState.DONE
        session.add(ticket)
        session.commit()
        assert resume_interrupted_orchestrations(session) == []

        ticket.state = TicketState.BLOCKED
        external = OrchestrationCallbackService(session).start_orchestration_run(
            ticket,
            driver=OrchestrationDriver.EXTERNAL_MCP,
            profile_slug="default",
        )
        external.status = OrchestrationRunStatus.FAILED
        session.add(external)
        session.commit()
        assert resume_interrupted_orchestrations(session) == []


def test_startup_resume_skips_manual_and_already_active_tickets(isolated_db):
    with Session(isolated_db) as session:
        seed_database(session)
        ticket = _ticket(session)
        ticket.state = TicketState.BLOCKED
        ticket.workflow_stage_status = StageStatus.BLOCKED
        ticket.blocking_issues = INTERRUPTED_RUN_MESSAGE
        session.add(ticket)
        session.commit()

        assert resume_interrupted_orchestrations(session) == []

        previous = OrchestrationCallbackService(session).start_orchestration_run(
            ticket,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            profile_slug="default",
        )
        assert previous.status == OrchestrationRunStatus.RUNNING
        assert resume_interrupted_orchestrations(session) == []


def test_interrupted_resumes_execute_serially():
    requests = [
        InterruptionResume("ticket-a", True, "test"),
        InterruptionResume("ticket-b", False, None),
    ]

    with patch(
        "loregarden.services.orchestration_recovery.execute_orchestration_background"
    ) as execute:
        schedule_interrupted_resumes(requests)

    assert execute.call_args_list == [
        call(
            "ticket-a",
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            auto_approve=True,
            stop_at_stage_key="test",
            timeout_seconds=None,
        ),
        call(
            "ticket-b",
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            auto_approve=False,
            stop_at_stage_key=None,
            timeout_seconds=None,
        ),
    ]


def test_lifespan_scans_for_resumes_after_reconciliation(isolated_db):
    order: list[str] = []

    async def run_lifespan():
        async with lifespan(None):
            pass

    with (
        patch("loregarden.main.init_db"),
        patch("loregarden.main.seed_database"),
        patch(
            "loregarden.main.fail_interrupted_runs",
            side_effect=lambda _session: order.append("agent-runs"),
        ),
        patch(
            "loregarden.main.fail_interrupted_orchestration_runs",
            side_effect=lambda _session: order.append("orchestration-runs"),
        ),
        patch("loregarden.main.fail_interrupted_triage_turns"),
        patch("loregarden.main.fail_interrupted_branch_triage_turns"),
        patch(
            "loregarden.main.settle_stranded_stages",
            side_effect=lambda _session: order.append("stranded-stages"),
        ),
        patch(
            "loregarden.main.resume_interrupted_orchestrations",
            side_effect=lambda _session: order.append("resume"),
        ),
    ):
        asyncio.run(run_lifespan())

    assert order == ["agent-runs", "orchestration-runs", "stranded-stages", "resume"]
