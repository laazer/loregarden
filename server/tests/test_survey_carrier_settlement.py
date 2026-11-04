"""A finished survey pass leaves its carrier done.

Six carriers sat open on 2026-11-03 although their passes had finished: the
runs died after the agent recorded the pass (lease expiry, a transition gate
that could not run), and only a clean run completion ever finished a carrier.
`record_reads` refuses a re-run once the pass is FINISHED, so a requeue could
not finish them either.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    OrchestrationRun,
    OrchestrationRunStatus,
    StageStatus,
    Ticket,
    TicketState,
)
from loregarden.models.domain.enums import utcnow
from loregarden.models.domain.survey_enums import SurveyPassOutcome, SurveyPassStatus
from loregarden.services.invariant_registry import FINISHED_PASSES_CLOSE_THEIR_CARRIER
from loregarden.services.orchestration import OrchestrationService
from loregarden.services.reference_survey import feeder
from loregarden.services.ticket_state_service import choose
from loregarden.services.workflow_state import set_stage_status
from sqlmodel import Session
from tests.survey_support import build_world, running_pass, write_tree


@pytest.fixture(name="ctx")
def ctx_fixture(isolated_db, tmp_path):
    session = Session(isolated_db)
    world = build_world(session)
    tree = write_tree(tmp_path / "clone", {"README.md": "r", "src/a.py": "a"})
    source, survey_pass = running_pass(session, world, tree)
    parent = session.get(Ticket, world.carrier_parent_id)
    carrier = feeder._create_carrier(session, source, survey_pass, parent, "brief")
    survey_pass.carrier_ticket_id = carrier.id
    session.add(survey_pass)
    session.commit()
    yield session, survey_pass, carrier
    session.close()


def _finish(session: Session, survey_pass) -> None:
    survey_pass.status = SurveyPassStatus.FINISHED
    survey_pass.outcome = SurveyPassOutcome.PARTIAL
    survey_pass.finished_at = utcnow()
    session.add(survey_pass)
    session.commit()


def _block_survey_stage(session: Session, carrier: Ticket) -> None:
    """What the lease sweep leaves: `survey` BLOCKED with the run gone."""
    instance, stages = OrchestrationService(session)._resolve_stages(carrier)
    set_stage_status(carrier, instance, stages, "survey", StageStatus.BLOCKED)
    choose(session, carrier, TicketState.BLOCKED, actor="orchestrator", emit=False)
    carrier.blocking_issues = "Agent run lease expired"
    session.add(instance)
    session.add(carrier)
    session.commit()


def test_a_carrier_blocked_after_its_pass_finished_is_finished(ctx):
    session, survey_pass, carrier = ctx
    _finish(session, survey_pass)
    _block_survey_stage(session, carrier)

    (violation,) = FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session)
    assert violation.subject_id == carrier.id
    assert violation.entered_at == survey_pass.finished_at

    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.repair(session, [violation]) == 1
    session.refresh(carrier)
    assert carrier.state is TicketState.DONE
    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session) == []


def test_a_carrier_still_on_its_survey_stage_is_finished(ctx):
    """The run ended without completing the stage, and nothing blocked it."""
    session, survey_pass, carrier = ctx
    _finish(session, survey_pass)

    violations = FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session)
    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.repair(session, violations) == 1
    session.refresh(carrier)
    assert carrier.state is TicketState.DONE


def test_a_carrier_whose_run_is_still_finishing_is_left_alone(ctx):
    """The control: a FINISHED pass waits minutes for its orchestration to end."""
    session, survey_pass, carrier = ctx
    _finish(session, survey_pass)
    session.add(
        OrchestrationRun(
            ticket_id=carrier.id,
            workspace_id=carrier.workspace_id,
            run_code="orch_live",
            status=OrchestrationRunStatus.RUNNING,
        )
    )
    session.commit()

    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session) == []


def test_a_pass_still_dispatched_is_not_a_violation(ctx):
    session, _survey_pass, _carrier = ctx

    assert FINISHED_PASSES_CLOSE_THEIR_CARRIER.detect(session) == []
