"""A finding a run files stays out of its parent's work until its scope is decided.

lg-milestone-that-1216's review lanes filed their findings as its children,
its child pass ran them, their review lanes filed more, and the milestone
could not finish until 37 run-filed tickets did. A finding is recorded against
the ticket it came from and held out of that ticket's work until its source's
spec decides it (`test_scope_round` covers the decision).
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    FindingDecision,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
    Ticket,
    TicketDiscovery,
    TicketState,
    WorkItemType,
    utcnow,
)
from loregarden.services import discovered_work
from loregarden.services.acceptance_criteria import serialize_criteria
from loregarden.services.agent_ticket_filing import (
    MAX_TICKETS_PER_DRIVE,
    MAX_TICKETS_PER_SOURCE,
    FilingRefused,
    check_filing_budget,
)
from loregarden.services.child_readiness import ordered_children
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.scope_round import decide_finding
from loregarden.services.seed import seed_database
from loregarden.services.ticket_rollup import derive_parent_state, has_children, reconcile_parent
from loregarden.services.undecided_finding_runs import undecided_findings_that_ran
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket

#: What the milestone is held to: a follow-up names it, since the milestone waits on it.
FAMILY_CRITERION = "Every child of the milestone lands"


@pytest.fixture(name="family")
def family_fixture(db_session: Session) -> tuple[Ticket, Ticket, AgentRun]:
    """A milestone with one child of its own, and a review run on that child."""
    seed_database(db_session)
    parent = make_workspace_ticket(db_session, "dw-parent")
    parent.work_item_type = WorkItemType.MILESTONE
    parent.acceptance_criteria_json = serialize_criteria([FAMILY_CRITERION])
    child = make_workspace_ticket(db_session, "dw-child")
    child.parent_ticket_id = parent.id
    child.work_item_type = WorkItemType.FEATURE
    drive = OrchestrationRun(
        run_code="orch_dw",
        ticket_id=child.id,
        workspace_id=child.workspace_id,
        status=OrchestrationRunStatus.RUNNING,
        driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
    )
    db_session.add_all([parent, child, drive])
    db_session.commit()
    run = AgentRun(
        run_code="run_dw",
        ticket_id=child.id,
        workspace_id=child.workspace_id,
        orchestration_run_id=drive.id,
        agent_id="architecture_reviewer",
        stage_key="review",
        status=RunStatus.RUNNING,
    )
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return parent, child, run


def _file(
    session: Session, run: AgentRun, title: str, *, parent: Ticket, kind: str = "feature"
) -> Ticket:
    """File a finding the way a review lane does: under the milestone, as a sibling."""
    created = json.loads(
        execute_tool(
            session,
            "loregarden_create_ticket",
            normalize_tool_arguments(
                "loregarden_create_ticket",
                {
                    "workspace_slug": "loregarden",
                    "title": title,
                    "work_item_type": kind,
                    "parent": parent.id,
                },
            ),
            orchestrated=True,
            run_id=run.id,
        )
    )
    return session.get(Ticket, created["id"])


def _decide(session: Session, ticket: Ticket, decision: FindingDecision) -> None:
    criterion = FAMILY_CRITERION if decision is FindingDecision.FOLLOW_UP else ""
    decide_finding(
        session,
        ticket,
        decision,
        reason="test decision",
        decided_by="operator",
        criterion=criterion,
    )


def test_a_filing_records_where_it_came_from_and_starts_undecided(db_session, family):
    parent, child, run = family
    finding = _file(db_session, run, "A finding", parent=parent)

    row = db_session.exec(
        select(TicketDiscovery).where(TicketDiscovery.filed_ticket_id == finding.id)
    ).one()
    assert (row.source_ticket_id, row.agent_run_id) == (child.id, run.id)
    assert row.orchestration_run_id == run.orchestration_run_id
    assert discovered_work.undecided(db_session, finding.id) is not None


def test_the_child_pass_skips_a_finding_until_it_is_its_parents_work(db_session, family):
    parent, child, run = family
    finding = _file(db_session, run, "A finding", parent=parent)
    assert [t.id for t in ordered_children(db_session, parent)] == [child.id]

    _decide(db_session, finding, FindingDecision.FOLLOW_UP)
    assert {t.id for t in ordered_children(db_session, parent)} == {child.id, finding.id}


def test_a_parent_finishes_on_its_own_work_while_a_finding_waits(db_session, family):
    parent, child, run = family
    _file(db_session, run, "A finding", parent=parent)
    child.state = TicketState.DONE
    db_session.add(child)
    db_session.commit()

    reconcile_parent(db_session, parent)
    assert parent.state is TicketState.DONE
    assert derive_parent_state([TicketState.DONE]) is TicketState.DONE


def test_a_ticket_whose_only_children_are_findings_owns_its_own_stages(db_session, family):
    _, child, run = family
    _file(db_session, run, "Filed under the reviewed ticket", parent=child, kind="bug")
    assert not has_children(db_session, child.id)


def test_no_dispatcher_can_start_an_undecided_finding(db_session, family):
    parent, _, run = family
    finding = _file(db_session, run, "A finding", parent=parent)

    with pytest.raises(discovered_work.UndecidedFinding, match="scope"):
        OrchestrationCallbackService(db_session).start_orchestration_run(
            finding, driver=OrchestrationDriver.EXTERNAL_MCP, profile_slug="default"
        )
    assert not db_session.exec(
        select(OrchestrationRun).where(OrchestrationRun.ticket_id == finding.id)
    ).first()


def test_a_deferred_finding_stays_out_of_its_parents_work(db_session, family):
    parent, child, run = family
    finding = _file(db_session, run, "A finding", parent=parent)
    _decide(db_session, finding, FindingDecision.DEFER)
    assert [t.id for t in ordered_children(db_session, parent)] == [child.id]
    assert discovered_work.undecided(db_session, finding.id) is None


def _recorded(session: Session, run: AgentRun, source: Ticket, title: str) -> Ticket:
    """A filing's discovery row written directly, to set up a cap without tripping another."""
    filed = make_workspace_ticket(session, title)
    session.add(
        TicketDiscovery(
            filed_ticket_id=filed.id,
            source_ticket_id=source.id,
            agent_run_id=run.id,
            orchestration_run_id=run.orchestration_run_id,
            filed_at=utcnow(),
        )
    )
    session.commit()
    return filed


def _run_on(session: Session, ticket: Ticket, code: str, **fields) -> AgentRun:
    run = AgentRun(
        run_code=code,
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id=fields.pop("agent_id", "static_reviewer"),
        stage_key=fields.pop("stage_key", "review"),
        status=fields.pop("status", RunStatus.RUNNING),
        **fields,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def test_a_drive_files_at_most_its_budget(db_session, family):
    _, _, run = family
    # Each filing from a different ticket of the drive, so no ticket's own budget is what trips.
    for index in range(MAX_TICKETS_PER_DRIVE):
        source = make_workspace_ticket(db_session, f"dw-drive-{index}")
        lane = _run_on(
            db_session,
            source,
            f"run_dw_drive_{index}",
            orchestration_run_id=run.orchestration_run_id,
        )
        _recorded(db_session, lane, source, f"dw-drive-finding-{index}")

    with pytest.raises(FilingRefused, match="drive has already filed"):
        check_filing_budget(db_session, run)


def test_a_ticket_files_at_most_its_lifetime_budget(db_session, family):
    """Decided or not: an earlier round's filings count against this round's (89 of 153 findings
    were filed by a second or later run on the same ticket)."""
    parent, child, run = family
    for index in range(MAX_TICKETS_PER_SOURCE):
        earlier = _run_on(db_session, child, f"run_dw_earlier_{index}", agent_id=f"lane_{index}")
        _decide(
            db_session,
            _recorded(db_session, earlier, child, f"dw-old-{index}"),
            FindingDecision.DEFER,
        )
    fresh = _run_on(db_session, child, "run_dw_fresh", agent_id="security_reviewer")

    with pytest.raises(FilingRefused, match="over its life"):
        check_filing_budget(db_session, fresh)


def test_a_re_review_files_nothing(db_session, family):
    """The same agent already finished the same stage on this ticket: what it finds now goes in
    its report. A run that crashed reported nothing, so it does not make the retry a re-review."""
    _, child, run = family
    crashed = _run_on(
        db_session, child, "run_dw_crashed", agent_id="static_reviewer", status=RunStatus.FAILED
    )
    retry = _run_on(db_session, child, "run_dw_retry", agent_id="static_reviewer")
    check_filing_budget(db_session, retry)

    retry.status = RunStatus.SUCCEEDED
    db_session.add(retry)
    db_session.commit()
    second_round = _run_on(db_session, child, "run_dw_round2", agent_id="static_reviewer")
    with pytest.raises(FilingRefused, match="re-review"):
        check_filing_budget(db_session, second_round)
    other_stage = _run_on(
        db_session, child, "run_dw_verify", agent_id="static_reviewer", stage_key="verify"
    )
    check_filing_budget(db_session, other_stage)
    assert crashed.status is RunStatus.FAILED


def test_a_finding_of_a_finding_files_nothing(db_session, family):
    """Planned work files a finding, and that finding may file; the finding it files may not.
    78 of 153 findings were filed against a finding, in chains up to six deep."""
    parent, child, run = family
    finding = _recorded(db_session, run, child, "dw-finding")
    on_finding = _run_on(db_session, finding, "run_dw_on_finding")
    check_filing_budget(db_session, on_finding)

    second = _recorded(db_session, on_finding, finding, "dw-finding-of-finding")
    on_second = _run_on(db_session, second, "run_dw_on_second")
    with pytest.raises(FilingRefused, match="finding of a finding"):
        check_filing_budget(db_session, on_second)


def test_a_follow_up_names_the_parent_criterion_it_is_needed_for(db_session, family):
    """It holds the parent open, so it says what for; by label or heading, as a reviewer cites."""
    parent, _, run = family
    finding = _file(db_session, run, "A finding", parent=parent)
    with pytest.raises(ValueError, match="must name the criterion"):
        decide_finding(
            db_session, finding, FindingDecision.FOLLOW_UP, reason="r", decided_by="operator"
        )
    with pytest.raises(ValueError, match="must name the criterion"):
        decide_finding(
            db_session,
            finding,
            FindingDecision.FOLLOW_UP,
            reason="r",
            decided_by="operator",
            criterion="Something the milestone never promised",
        )

    row = decide_finding(
        db_session,
        finding,
        FindingDecision.FOLLOW_UP,
        reason="r",
        decided_by="operator",
        criterion="Every child of the milestone lands (this one does not yet)",
    )
    assert row.decision is FindingDecision.FOLLOW_UP
    assert FAMILY_CRITERION in row.decision_reason


def test_a_parent_with_no_criteria_waits_on_no_finding(db_session, family):
    """lg-code-knowledge-273 held seven follow-ups and states no criteria: defer, or give it one."""
    parent, _, run = family
    parent.acceptance_criteria_json = serialize_criteria([])
    db_session.add(parent)
    db_session.commit()
    finding = _file(db_session, run, "A finding", parent=parent)

    with pytest.raises(ValueError, match="states no acceptance criteria"):
        decide_finding(
            db_session,
            finding,
            FindingDecision.FOLLOW_UP,
            reason="r",
            decided_by="operator",
            criterion=FAMILY_CRITERION,
        )
    _decide(db_session, finding, FindingDecision.DEFER)
    assert discovered_work.undecided(db_session, finding.id) is None


def test_an_undecided_finding_that_ran_is_reported(db_session, family):
    parent, _, run = family
    finding = _file(db_session, run, "A finding", parent=parent)
    assert undecided_findings_that_ran(db_session) == []

    db_session.add(
        OrchestrationRun(
            run_code="orch_bypass",
            ticket_id=finding.id,
            workspace_id=finding.workspace_id,
            status=OrchestrationRunStatus.RUNNING,
            driver=OrchestrationDriver.BUILTIN_AUTOPILOT,
            started_at=utcnow(),
        )
    )
    db_session.commit()

    (violation,) = undecided_findings_that_ran(db_session)
    assert violation.subject_id == finding.id
