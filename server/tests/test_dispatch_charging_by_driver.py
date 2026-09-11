"""Every dispatch is charged to the retry budget except the one somebody else
already charged.

The test used to be "does this run have an orchestration run at all", on the
premise that a run with one came from the autopilot loop, which charges itself in
`enforce_stage_retry_budget` before it calls `start_run`. Every other driver also
opens an orchestration run, so every other driver dispatched for free: 165 of 194
`external_mcp` runs measured landed on tickets carrying no dispatch marker at
all, one stage ran 12 times against a counter reading zero, and across the whole
life of the database the breaker had never once fired.
"""

from loregarden.models.domain import (
    OrchestrationDriver,
    RunStatus,
    Ticket,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.seed import seed_database
from loregarden.services.stage_retry_budget import count_stage_dispatches
from sqlmodel import Session, select

STAGE = "testing"


def _ticket(session: Session) -> Ticket:
    seed_database(session)
    ticket = session.exec(
        select(Ticket).where(Ticket.legacy_external_id == "03-wire-cli-agent-runner")
    ).first()
    assert ticket is not None
    return ticket


def _dispatch(session: Session, ticket: Ticket, driver: OrchestrationDriver | None) -> None:
    """One dispatch of `STAGE`, as the given driver would make it."""
    orch_run_id = None
    if driver is not None:
        orch_run_id = (
            OrchestrationCallbackService(session)
            .start_orchestration_run(ticket, driver=driver, profile_slug="default")
            .id
        )
    run = OrchestrationService(session).start_run(
        ticket, stage_key=STAGE, orchestration_run_id=orch_run_id
    )
    # Settle it so the next dispatch is not waved through as a pass already in
    # flight (`dispatch_pass_open`).
    OrchestrationService(session).complete_run(run, status=RunStatus.CANCELLED)


def test_an_external_harness_dispatch_is_charged(isolated_db):
    with Session(isolated_db) as session:
        ticket = _ticket(session)

        _dispatch(session, ticket, OrchestrationDriver.EXTERNAL_MCP)

        assert count_stage_dispatches(session, ticket.id, STAGE) == 1


def test_a_manual_stage_dispatch_is_charged(isolated_db):
    with Session(isolated_db) as session:
        ticket = _ticket(session)

        _dispatch(session, ticket, OrchestrationDriver.MANUAL_STAGE)

        assert count_stage_dispatches(session, ticket.id, STAGE) == 1


def test_a_dispatch_with_no_orchestration_run_is_still_charged(isolated_db):
    """The one case that always was — kept so the fix cannot regress it."""
    with Session(isolated_db) as session:
        ticket = _ticket(session)

        _dispatch(session, ticket, None)

        assert count_stage_dispatches(session, ticket.id, STAGE) == 1


def test_the_autopilot_loop_is_not_charged_twice(isolated_db):
    """It charges itself pre-dispatch; charging again would halve its budget."""
    with Session(isolated_db) as session:
        ticket = _ticket(session)

        _dispatch(session, ticket, OrchestrationDriver.BUILTIN_AUTOPILOT)

        assert count_stage_dispatches(session, ticket.id, STAGE) == 0


def test_repeated_dispatches_under_one_external_run_accumulate(isolated_db):
    """The shape a terminal agent actually makes: one orchestration run it holds
    open, and a `loregarden_start_stage` per attempt. The counter has to add up
    across those, or a budget cannot be reached — which is the state the breaker
    was in for the whole life of the database.
    """
    with Session(isolated_db) as session:
        ticket = _ticket(session)
        orch_run = OrchestrationCallbackService(session).start_orchestration_run(
            ticket, driver=OrchestrationDriver.EXTERNAL_MCP, profile_slug="default"
        )
        orch = OrchestrationService(session)

        for _ in range(3):
            run = orch.start_run(ticket, stage_key=STAGE, orchestration_run_id=orch_run.id)
            orch.complete_run(run, status=RunStatus.CANCELLED)

        assert count_stage_dispatches(session, ticket.id, STAGE) == 3


def test_several_members_joining_one_open_pass_cost_one_attempt(isolated_db):
    """The risk this change carries: the guard's seam fires once per *member*,
    not once per dispatch, so routing external runs through it could charge a
    3-member review three times — tearing the last affordable pass in half.
    `dispatch_pass_open` is what prevents that (a live run of the stage means a
    pass is already in flight), and it now has to hold for this driver too.
    """
    with Session(isolated_db) as session:
        ticket = _ticket(session)
        orch_run = OrchestrationCallbackService(session).start_orchestration_run(
            ticket, driver=OrchestrationDriver.EXTERNAL_MCP, profile_slug="default"
        )
        orch = OrchestrationService(session)
        members = ("architecture_reviewer", "static_qa", "security_reviewer")

        for agent_id in members:
            orch.start_run(
                ticket,
                stage_key="review",
                orchestration_run_id=orch_run.id,
                agent_id=agent_id,
            )
            session.refresh(ticket)

        assert count_stage_dispatches(session, ticket.id, "review") == 1
