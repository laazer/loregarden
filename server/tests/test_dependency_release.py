"""A wait on another ticket is a hold, not a block, and it ends when that ticket lands.

lg-milestone-that-1315's triage reported its wait on 1314 as `work`, got a
repair turn to report it again, and was parked by hand; nothing would have
resumed it when 1314 landed. Its plan stage's rework reroute, meanwhile, was
read by the block sweep as an unsettled block and filed as a missed writer.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    AgentRun,
    BlockKind,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    OrchestratorDecision,
    RunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.services.block_settlement import unclassified_blocks
from loregarden.services.dependency_hold import record_hold
from loregarden.services.dependency_readiness import UnmetPrerequisite, UnmetReason
from loregarden.services.dependency_release import (
    WAITING_TAG,
    mark_parked_waits,
    releasable_tickets,
    release_tickets,
)
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.run_completion import advance_stage_after_run
from loregarden.services.seed import seed_database
from loregarden.services.stage_report import StageReport
from loregarden.services.ticket_dependencies import TicketDependencyService
from loregarden.services.ticket_tags import load_tags
from loregarden.services.workflow_routing import apply_stage_route
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket
from tests.history_helpers import decision_kinds, decision_payloads

STAGE = "plan"


@pytest.fixture(name="running")
def running_fixture(db_session: Session) -> tuple[Ticket, AgentRun, Ticket]:
    """A ticket whose `plan` run just finished, and the ticket it will say it waits on."""
    seed_database(db_session)
    prerequisite = make_workspace_ticket(db_session, "release-prereq")
    ticket = make_workspace_ticket(db_session, "release-dependent")
    orch = OrchestrationService(db_session)
    orch.ensure_workflow_instance(ticket, commit=True)
    ticket.workflow_stage_key = STAGE
    ticket.workflow_stage_status = StageStatus.RUNNING
    db_session.add(ticket)
    run = AgentRun(
        run_code="run_release",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="planner",
        stage_key=STAGE,
        status=RunStatus.SUCCEEDED,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(ticket)
    db_session.refresh(run)
    return ticket, run, prerequisite


def _waiting_report(*refs: str) -> StageReport:
    return StageReport(
        status="blocked",
        confidence=0.9,
        reroute_to_stage=None,
        reroute_context="Needs the other ticket's implementation first.",
        blocked_kind=BlockKind.TICKETS,
        waiting_on=list(refs),
    )


def _complete(db_session: Session, ticket: Ticket, run: AgentRun, report: StageReport) -> None:
    orch = OrchestrationService(db_session)
    instance, _ = orch._resolve_stages(ticket)
    advance_stage_after_run(orch, ticket, run, report, RunStatus.SUCCEEDED, "")
    db_session.commit()
    db_session.refresh(ticket)


def test_a_reported_wait_links_the_tickets_and_holds_without_blocking(db_session, running):
    ticket, run, prerequisite = running

    _complete(db_session, ticket, run, _waiting_report(prerequisite.external_id))

    assert TicketDependencyService(db_session).prerequisites(ticket.id) == [prerequisite.id]
    assert ticket.state is not TicketState.BLOCKED
    assert ticket.workflow_stage_status is StageStatus.PENDING
    assert ticket.block_kind is None
    kinds = decision_kinds(db_session, ticket.id)
    assert OrchestratorDecision.HELD_ON_PREREQUISITES.value in kinds
    # The wait is not an agent's to shorten, so it gets no repair turn.
    assert OrchestratorDecision.DISPATCHED_REPAIR.value not in kinds


def _assert_fell_back_to_a_block(db_session: Session, ticket: Ticket, *phrases: str) -> None:
    """Settled as an ordinary block, whose message says why the wait was not held.

    Where it went next is the block's own business (a `work` block takes its
    repair turn), so this reads the settlement, not the stage.
    """
    payloads = decision_payloads(db_session, ticket.id)
    kinds = [payload["decision"] for payload in payloads]
    assert OrchestratorDecision.HELD_ON_PREREQUISITES.value not in kinds
    assert OrchestratorDecision.CLASSIFIED_BLOCK.value in kinds
    # The block's message rides on whichever settlement carried it on: the
    # repair turn's brief, or the escalation to a person.
    message = json.dumps(payloads)
    for phrase in phrases:
        assert phrase in message


def test_a_wait_on_a_ticket_that_does_not_exist_blocks_and_says_so(db_session, running):
    ticket, run, _ = running

    _complete(db_session, ticket, run, _waiting_report("00000000-0000-4000-8000-000000000009"))

    _assert_fell_back_to_a_block(
        db_session, ticket, "could not be linked", "00000000-0000-4000-8000-000000000009"
    )


def test_a_wait_on_work_already_landed_blocks_rather_than_looping(db_session, running):
    ticket, run, prerequisite = running
    prerequisite.state = TicketState.WONT_DO
    db_session.add(prerequisite)
    db_session.commit()

    _complete(db_session, ticket, run, _waiting_report(prerequisite.external_id))

    _assert_fell_back_to_a_block(db_session, ticket, "already done and landed")


def test_a_rework_reroute_is_not_an_unsettled_block(db_session, running):
    """The repro of 1315's false `invariant_reentered`: a reroute leaves its
    feedback in `blocking_issues` and the stage pending, with no run behind it."""
    ticket, _, _ = running
    orch = OrchestrationService(db_session)
    instance, stages = orch._resolve_stages(ticket)
    upstream, current = stages[0].key, stages[1].key
    ticket.workflow_stage_key = current
    db_session.add(ticket)
    db_session.commit()
    apply_stage_route(
        ticket,
        instance,
        stages,
        orch._resolve_transitions(ticket),
        from_key=current,
        outcome="reject",
        blocking_issues="The plan cannot start until 1314 lands.",
        next_stage_key=upstream,
    )
    db_session.commit()
    db_session.refresh(ticket)
    assert ticket.blocking_issues, "the premise: the feedback is in blocking_issues"
    assert ticket.workflow_stage_status is StageStatus.PENDING

    assert ticket.id not in {t.id for t in unclassified_blocks(db_session)}


def _held_in_progress(db_session: Session, ticket: Ticket, prerequisite: Ticket) -> None:
    """A drive that paused on a hold: the builtin loop's path, recorded as it records it."""
    TicketDependencyService(db_session).add_dependency(ticket.id, prerequisite.id)
    ticket.workflow_stage_status = StageStatus.PENDING
    db_session.add(ticket)
    db_session.add(
        OrchestrationRun(
            run_code="orch_paused",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            status=OrchestrationRunStatus.SUCCEEDED,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
        )
    )
    db_session.commit()
    record_hold(
        db_session,
        ticket,
        [UnmetPrerequisite(prerequisite, UnmetReason.NOT_DONE)],
        stage_key=ticket.workflow_stage_key,
    )


def test_a_held_ticket_is_resumed_once_its_prerequisite_lands(db_session, running):
    ticket, _, prerequisite = running
    _held_in_progress(db_session, ticket, prerequisite)
    assert releasable_tickets(db_session) == []

    prerequisite.state = TicketState.WONT_DO
    db_session.add(prerequisite)
    db_session.commit()
    assert [t.id for t in releasable_tickets(db_session)] == [ticket.id]

    with patch("loregarden.services.dependency_release.schedule_interrupted_resumes") as start:
        released = release_tickets(db_session, releasable_tickets(db_session))

    assert released == [ticket.external_id]
    assert OrchestratorDecision.RELEASED_ON_PREREQUISITES.value in decision_kinds(
        db_session, ticket.id
    )
    (requests,) = start.call_args.args
    assert [request.ticket_id for request in requests] == [ticket.id]
    assert releasable_tickets(db_session) == []


def test_a_parked_ticket_seen_waiting_is_released_when_nothing_holds_it(db_session, running):
    ticket, _, prerequisite = running
    TicketDependencyService(db_session).add_dependency(ticket.id, prerequisite.id)
    ticket.state = TicketState.PARKED
    db_session.add(ticket)
    db_session.commit()

    mark_parked_waits(db_session)
    db_session.refresh(ticket)
    assert WAITING_TAG in load_tags(ticket.tags_json)
    assert releasable_tickets(db_session) == []

    prerequisite.state = TicketState.WONT_DO
    db_session.add(prerequisite)
    db_session.commit()
    with patch("loregarden.services.dependency_release.schedule_interrupted_resumes"):
        assert release_tickets(db_session, releasable_tickets(db_session)) == [ticket.external_id]

    db_session.refresh(ticket)
    assert ticket.state is not TicketState.PARKED
    assert WAITING_TAG not in load_tags(ticket.tags_json)


def test_a_parked_ticket_never_seen_waiting_stays_parked(db_session, running):
    """Parked for some other reason, with prerequisites that were already met."""
    ticket, _, prerequisite = running
    prerequisite.state = TicketState.WONT_DO
    db_session.add(prerequisite)
    TicketDependencyService(db_session).add_dependency(ticket.id, prerequisite.id)
    ticket.state = TicketState.PARKED
    db_session.add(ticket)
    db_session.commit()

    mark_parked_waits(db_session)

    assert releasable_tickets(db_session) == []
    db_session.refresh(ticket)
    assert ticket.state is TicketState.PARKED


def test_unparking_by_hand_drops_the_tag(db_session, running):
    ticket, _, prerequisite = running
    TicketDependencyService(db_session).add_dependency(ticket.id, prerequisite.id)
    ticket.state = TicketState.PARKED
    db_session.add(ticket)
    db_session.commit()
    mark_parked_waits(db_session)

    ticket.state = TicketState.BACKLOG
    db_session.add(ticket)
    db_session.commit()
    mark_parked_waits(db_session)

    db_session.refresh(ticket)
    assert WAITING_TAG not in load_tags(ticket.tags_json)
    assert db_session.exec(select(Ticket).where(Ticket.id == ticket.id)).one()
