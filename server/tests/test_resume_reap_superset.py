"""The reaper must always be able to claim what the resume lookup declined.

`external_harness._resumable_stage_run` adopts an in-flight external run, and
returns None on ambiguity — two runs in flight, and no fact in the rows saying
which one the harness means. Declining is only safe because the caller then reaps:
`run_service.fail_interrupted_runs`, ticket-scoped, selects a SUPERSET of
everything the lookup could have adopted.

That superset relationship is the design, not drift. It was stated nowhere, and a
locally-correct edit — adding `external_harness IS NOT NULL` to the ticket-scoped
branch, matching the unscoped one — would strand the ambiguous case RUNNING with
nothing that ever settles it.

Deliberately NOT tested by asserting the two see the same set: they must not.
lg-workflow-integrity-602's original proposal (one shared selector both consume)
was rejected on review, because collapsing them forces a choice between the
narrow and broad semantics.
"""

from datetime import datetime, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    RunStatus,
    Ticket,
)
from loregarden.services.external_harness import _resumable_stage_run
from loregarden.services.run_service import fail_interrupted_runs
from loregarden.services.triage_service import TRIAGE_AGENT_ID
from sqlmodel import Session
from tests.factories import make_workspace_ticket

STAGE = "implement"


def _run(
    session: Session,
    ticket: Ticket,
    *,
    code: str,
    status: RunStatus = RunStatus.RUNNING,
    harness: ExternalHarness | None = ExternalHarness.CLAUDE_CODE,
    agent_id: str = "backend_implementer",
    stage_key: str = STAGE,
) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=agent_id,
        stage_key=stage_key,
        status=status,
        external_harness=harness,
        started_at=datetime.now(timezone.utc),
        last_seen_at=datetime.now(timezone.utc),
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


@pytest.fixture
def ticket(db_session: Session) -> Ticket:
    return make_workspace_ticket(db_session, "rrs-1")


def _reaped(db_session: Session, ticket: Ticket) -> set[str]:
    """Run the REAL reaper and report what it claimed.

    Deliberately not a replica of its query. An earlier version of this file
    rebuilt the selector inline, and a control proved the consequence: adding the
    `external_harness IS NOT NULL` filter to the real function — the exact edit
    this invariant forbids — left every test passing, because they were asserting
    against a copy. A test that reimplements the code it guards cannot see that
    code change.
    """
    claimed = fail_interrupted_runs(db_session, ticket_id=ticket.id)
    return {run.id for run in claimed}


def test_the_reaper_selects_a_superset_of_what_resume_can_adopt(
    db_session: Session, ticket: Ticket
):
    """AC2. The single run case: resume adopts it, and the reaper could too."""
    run = _run(db_session, ticket, code="rrs_single")

    adopted = _resumable_stage_run(db_session, ticket, STAGE)
    assert adopted is not None and adopted.id == run.id

    assert {adopted.id} <= _reaped(db_session, ticket), (
        "the reaper must be able to claim the run resume adopted"
    )


def test_what_resume_declines_on_ambiguity_is_still_reapable(db_session: Session, ticket: Ticket):
    """The case the invariant exists for. Two in flight: resume declines, and
    BOTH must remain claimable — otherwise they sit RUNNING forever."""
    first = _run(db_session, ticket, code="rrs_a")
    second = _run(db_session, ticket, code="rrs_b")

    assert _resumable_stage_run(db_session, ticket, STAGE) is None, "ambiguity declines"

    assert {first.id, second.id} <= _reaped(db_session, ticket), (
        "resume declined these; if the reaper cannot claim them nothing ever settles them"
    )


def test_the_reaper_also_claims_runs_resume_could_never_adopt(db_session: Session, ticket: Ticket):
    """Superset, not equality — which is why one shared selector was rejected.

    A non-external run and an AWAITING_PERMISSION run are both outside the
    resume lookup's filter (it requires `external_harness IS NOT NULL` and
    RUNNING), and both must still be reapable.
    """
    internal = _run(db_session, ticket, code="rrs_internal", harness=None)
    awaiting = _run(db_session, ticket, code="rrs_awaiting", status=RunStatus.AWAITING_PERMISSION)

    assert _resumable_stage_run(db_session, ticket, STAGE) is None

    assert {internal.id, awaiting.id} <= _reaped(db_session, ticket), (
        "neither is adoptable by resume, so both depend on the reaper claiming them"
    )


def test_a_triage_turn_is_outside_both(db_session: Session, ticket: Ticket):
    """The one exclusion the two genuinely share, and for the same reason:
    a chat turn routed through complete_run would advance the workflow off a
    chat message."""
    turn = _run(db_session, ticket, code="rrs_triage", agent_id=TRIAGE_AGENT_ID)

    assert _resumable_stage_run(db_session, ticket, STAGE) is None
    assert turn.id not in _reaped(db_session, ticket)


def test_an_internal_run_is_reaped_though_resume_could_never_adopt_it(
    db_session: Session, ticket: Ticket
):
    """The case that catches the forbidden edit.

    Adding `external_harness IS NOT NULL` to the ticket-scoped branch — matching
    the unscoped one, and locally plausible — would skip this run. Resume cannot
    adopt it either, so nothing would ever settle it and the stage sits RUNNING
    forever. This is the test that fails when someone makes that edit.
    """
    internal = _run(db_session, ticket, code="rrs_internal_e2e", harness=None)
    assert _resumable_stage_run(db_session, ticket, STAGE) is None

    fail_interrupted_runs(db_session, ticket_id=ticket.id)

    db_session.refresh(internal)
    assert internal.status is RunStatus.FAILED, (
        "the reaper must claim a run resume cannot adopt, or it is stranded"
    )
