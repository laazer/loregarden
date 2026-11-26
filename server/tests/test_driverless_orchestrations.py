"""No start the control plane makes opens an orchestration nobody drives.

`external_mcp` spawns nothing: it waits for a terminal agent. A workspace whose
profile names it (`default.yaml` does, for every workspace without one) used to
get that driver for approvals, lanes and initiative autopilot too. Each opened a
run that read "Running" with no agent until its lease expired.
"""

from __future__ import annotations

import ast
from pathlib import Path
from unittest.mock import patch

import pytest
from loregarden.models.domain import (
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    Ticket,
)
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.orchestration_profile import (
    OrchestrationProfile,
    resolve_orchestration_profile,
)
from loregarden.services.orchestration_resume import resume_orchestration
from loregarden.services.queue_dispatch import LaneDispatch
from loregarden.services.run_service import RunService
from loregarden.services.work_admission import OrchestrationStart, start_orchestration_admitted
from sqlmodel import Session, select
from tests.factories import make_ticket, make_workspace
from tests.source_ast import parse_path

PACKAGE = Path(__file__).resolve().parents[1] / "loregarden"
BUILTIN = OrchestrationDriver.BUILTIN_AUTOPILOT
EXTERNAL = OrchestrationDriver.EXTERNAL_MCP

#: The one reader of the profile's stated driver that starts nothing: the settings view.
STATED_DRIVER_READERS = {"api/orchestration.py"}


@pytest.fixture(name="ticket")
def ticket_fixture(db_session: Session) -> Ticket:
    """A ticket in a workspace with no profile of its own, as loremaker's is."""
    workspace = make_workspace(db_session, slug="no-profile-of-its-own")
    assert resolve_orchestration_profile(workspace).driver is EXTERNAL, "the premise"
    return make_ticket(db_session, workspace_id=workspace.id, ticket_id="driverless-1")


def _runs(session: Session, ticket: Ticket) -> list[OrchestrationRun]:
    return list(
        session.exec(select(OrchestrationRun).where(OrchestrationRun.ticket_id == ticket.id))
    )


def test_no_start_path_reads_the_profiles_stated_driver():
    """Every start resolves through `unattended_driver`; a new one must too."""
    readers = set()
    for path in PACKAGE.rglob("*.py"):
        for node in ast.walk(parse_path(path)):
            if (
                isinstance(node, ast.Attribute)
                and node.attr == "driver"
                and "profile" in ast.unparse(node.value).lower()
            ):
                readers.add(path.relative_to(PACKAGE).as_posix())
    assert readers == STATED_DRIVER_READERS


@pytest.mark.parametrize(
    ("stated", "unattended"),
    [
        (EXTERNAL, BUILTIN),
        (BUILTIN, BUILTIN),
        (OrchestrationDriver.MANUAL_STAGE, OrchestrationDriver.MANUAL_STAGE),
    ],
)
def test_an_external_profile_is_driven_here_when_nobody_names_a_driver(stated, unattended):
    assert OrchestrationProfile(slug="p", driver=stated).unattended_driver is unattended


def test_a_claim_naming_no_driver_is_builtin(db_session: Session, ticket: Ticket):
    """The Run button, the chat loop and a lane all claim this way."""
    claim = OrchestrationCallbackService(db_session).claim_orchestration_run(ticket)
    assert claim.driver is BUILTIN


def test_a_claim_naming_the_external_driver_keeps_it(db_session: Session, ticket: Ticket):
    claim = OrchestrationCallbackService(db_session).claim_orchestration_run(
        ticket, driver=EXTERNAL
    )
    assert claim.driver is EXTERNAL


def test_a_scheduled_start_runs_the_builtin_orchestrator(db_session: Session, ticket: Ticket):
    """What an approval's resume reaches on its background thread."""
    with patch("loregarden.services.run_service.BuiltinOrchestrator") as builtin:
        RunService(db_session).orchestrate_ticket(ticket)
    builtin.return_value.execute.assert_called_once()
    assert _runs(db_session, ticket) == []


def test_a_lane_drives_what_autopilot_queued(db_session: Session, ticket: Ticket):
    with patch("loregarden.services.queue_dispatch.schedule_orchestration"):
        run = LaneDispatch(db_session).dispatch_orchestration(
            ticket, auto_approve=True, stop_at_stage_key=None
        )
    assert run is not None
    assert run.driver is BUILTIN


def test_an_admitted_start_naming_no_driver_is_builtin(db_session: Session, ticket: Ticket):
    with patch("loregarden.services.work_admission.BuiltinOrchestrator") as builtin:
        builtin.return_value.execute.side_effect = lambda *_a, **_k: _runs(db_session, ticket)[0]
        _, run = start_orchestration_admitted(
            db_session,
            OrchestrationCallbackService(db_session),
            OrchestrationStart(ticket_id=ticket.id),
        )
    assert run.driver is BUILTIN


def test_an_admitted_start_naming_the_external_driver_keeps_it(db_session: Session, ticket: Ticket):
    """A terminal agent says so, and still gets its own run."""
    _, run = start_orchestration_admitted(
        db_session,
        OrchestrationCallbackService(db_session),
        OrchestrationStart(ticket_id=ticket.id, driver=EXTERNAL),
    )
    assert run.driver is EXTERNAL
    assert run.status is OrchestrationRunStatus.RUNNING


@pytest.mark.parametrize(("previous", "carried"), [(EXTERNAL, None), (BUILTIN, BUILTIN)])
def test_a_continuation_does_not_carry_a_driver_that_has_left(
    db_session: Session, ticket: Ticket, previous, carried
):
    db_session.add(
        OrchestrationRun(
            run_code="orch_over",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            status=OrchestrationRunStatus.FAILED,
            driver=previous,
        )
    )
    db_session.commit()
    with patch("loregarden.services.orchestration_resume.schedule_orchestration") as scheduled:
        resume_orchestration(db_session, ticket, carry_driver=True)
    assert scheduled.call_args.kwargs["driver"] is carried
