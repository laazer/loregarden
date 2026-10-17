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


def test_the_reaper_says_why_it_is_not_the_per_dispatch_settle():
    """AC3 of lg-milestone-that-976, the comment arm.

    Four effects settle a dispatch that never attempted the work: the run
    fails, the dispatch charge comes back, the stage goes back to PENDING and a
    decision is recorded. This reaper does the first and none of the others, and
    that difference is deliberate — it is the boot/orphan sweep, claiming every
    run it can see, so refunding charges and forcing stages to PENDING would
    hand retry budget back across every live stage on restart.

    Pinned as a comment rather than as a call because the call arm is the bug.
    The ticket's own description attributed the decision-recording orphan shape
    to this function; it is `settle_orphaned_agent_runs`, in the same module.
    A reader who makes that mistake while holding an editor turns a boot sweep
    into a budget reset, and the superset INVARIANT above goes with it.
    """
    import ast
    import inspect

    from loregarden.services import run_service

    source = inspect.getsource(run_service)
    reaper = next(
        node
        for node in ast.walk(ast.parse(source))
        if isinstance(node, ast.FunctionDef)
        and node.name == "fail_interrupted_runs"  # py-org: allow-isinstance - an ast node
    )
    # The comment block directly above `def` counts as much as one inside the
    # body: it is where a reader looking for "how does this differ from the
    # settle?" looks first, and `ast.get_source_segment` starts at the `def`
    # line, so reading only the segment would have failed the arm AC3 asks for.
    lines = source.splitlines()
    lead: list[str] = []
    cursor = reaper.lineno - 2
    while cursor >= 0 and lines[cursor].strip().startswith("#"):
        lead.insert(0, lines[cursor])
        cursor -= 1
    text = ast.get_source_segment(source, reaper) or ""
    comments = "\n".join(line for line in lead + text.splitlines() if line.strip().startswith("#"))
    assert "settle_failed_handoff" in comments, (
        "nothing on fail_interrupted_runs says how it differs from the per-dispatch "
        "settle in services/handoff_settle.py, or why: the next reader is as likely to "
        "make this one refund and reset stages as to leave it alone"
    )
    assert "settle_orphaned_agent_runs" in comments, (
        "the comment does not correct the record: the orphan shape that records a "
        "decision is settle_orphaned_agent_runs, not this function"
    )
    called = {
        node.func.attr
        if isinstance(node.func, ast.Attribute)
        else getattr(node.func, "id", "")  # py-org: allow-isinstance - an ast node
        for node in ast.walk(reaper)
        if isinstance(node, ast.Call)  # py-org: allow-isinstance - an ast node
    }
    forbidden = called & {
        "refund_stage_dispatch_charged_before",
        "refund_stage_dispatch_charged_since",
        "finalize_stage",
        "record_orchestrator_decision",
    }
    assert forbidden == set(), (
        f"the boot sweep gained the per-dispatch settle's effects ({sorted(forbidden)}): "
        "it claims every in-flight run on a ticket, so refunding charges or forcing "
        "stages to PENDING hands retry budget back across every live stage on restart"
    )
