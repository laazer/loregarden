"""Work a run discovers stays where it was filed until someone triages it.

lg-milestone-that-1216's review lanes filed their findings as its children,
its child pass ran them, their review lanes filed more, and the milestone
could not finish until 37 run-filed tickets did. A finding is recorded against
the ticket it came from and held out of that ticket's work until triage
promotes it or closes it.
"""

from __future__ import annotations

import json

import pytest
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    DiscoveryTriage,
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
from loregarden.services.agent_ticket_filing import (
    MAX_TICKETS_PER_DRIVE,
    MAX_UNTRIAGED_PER_SOURCE,
    FilingRefused,
    check_filing_budget,
)
from loregarden.services.child_readiness import ordered_children
from loregarden.services.orchestration_callbacks import OrchestrationCallbackService
from loregarden.services.seed import seed_database
from loregarden.services.ticket_rollup import derive_parent_state, has_children, reconcile_parent
from loregarden.services.untriaged_discovery_runs import untriaged_discoveries_that_ran
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket


@pytest.fixture(name="family")
def family_fixture(db_session: Session) -> tuple[Ticket, Ticket, AgentRun]:
    """A milestone with one child of its own, and a review run on that child."""
    seed_database(db_session)
    parent = make_workspace_ticket(db_session, "dw-parent")
    parent.work_item_type = WorkItemType.MILESTONE
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


def _triage(session: Session, ticket: Ticket, action: str) -> dict:
    return json.loads(
        execute_tool(
            session,
            "loregarden_triage_discovery",
            normalize_tool_arguments(
                "loregarden_triage_discovery", {"ticket_id": ticket.id, "action": action}
            ),
        )
    )


def test_a_filing_records_where_it_came_from_and_starts_untriaged(db_session, family):
    parent, child, run = family
    finding = _file(db_session, run, "A finding", parent=parent)

    row = db_session.exec(
        select(TicketDiscovery).where(TicketDiscovery.filed_ticket_id == finding.id)
    ).one()
    assert (row.source_ticket_id, row.agent_run_id) == (child.id, run.id)
    assert row.orchestration_run_id == run.orchestration_run_id
    assert discovered_work.untriaged(db_session, finding.id) is not None


def test_the_child_pass_skips_an_untriaged_finding_until_it_is_promoted(db_session, family):
    parent, child, run = family
    finding = _file(db_session, run, "A finding", parent=parent)
    assert [t.id for t in ordered_children(db_session, parent)] == [child.id]

    assert _triage(db_session, finding, "promote")["triaged_by"].endswith("promote")
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


def test_no_dispatcher_can_start_an_untriaged_finding(db_session, family):
    parent, _, run = family
    finding = _file(db_session, run, "A finding", parent=parent)

    with pytest.raises(discovered_work.UntriagedDiscovery, match="triage"):
        OrchestrationCallbackService(db_session).start_orchestration_run(
            finding, driver=OrchestrationDriver.EXTERNAL_MCP, profile_slug="default"
        )
    assert not db_session.exec(
        select(OrchestrationRun).where(OrchestrationRun.ticket_id == finding.id)
    ).first()


def test_closing_a_finding_closes_its_ticket(db_session, family):
    parent, _, run = family
    finding = _file(db_session, run, "A finding", parent=parent)
    _triage(db_session, finding, "close")
    db_session.refresh(finding)
    assert finding.state is TicketState.WONT_DO
    assert discovered_work.untriaged(db_session, finding.id) is None


def test_triage_refuses_a_ticket_no_run_filed(db_session, family):
    parent, _, _ = family
    with pytest.raises(ValueError, match="not an untriaged discovery"):
        _triage(db_session, parent, "promote")


def test_a_drive_files_at_most_its_budget(db_session, family):
    parent, child, run = family
    for index in range(MAX_TICKETS_PER_DRIVE):
        sibling = AgentRun(
            run_code=f"run_dw_{index}",
            ticket_id=child.id,
            workspace_id=child.workspace_id,
            orchestration_run_id=run.orchestration_run_id,
            agent_id="static_reviewer",
            stage_key="review",
            status=RunStatus.RUNNING,
        )
        db_session.add(sibling)
        db_session.commit()
        finding = _file(db_session, sibling, f"Finding {index}", parent=parent)
        # Promoted, so the per-source cap is not the one that trips.
        discovered_work.triage(db_session, finding, DiscoveryTriage.PROMOTE, actor="test")
        db_session.commit()

    with pytest.raises(FilingRefused, match="drive has already filed"):
        check_filing_budget(db_session, run)


def test_a_ticket_holds_at_most_its_untriaged_budget(db_session, family):
    parent, child, run = family
    for index in range(MAX_UNTRIAGED_PER_SOURCE):
        db_session.add(
            TicketDiscovery(
                filed_ticket_id=make_workspace_ticket(db_session, f"dw-old-{index}").id,
                source_ticket_id=child.id,
                agent_run_id=run.id,
                filed_at=utcnow(),
            )
        )
    db_session.commit()
    fresh = AgentRun(
        run_code="run_dw_fresh",
        ticket_id=child.id,
        workspace_id=child.workspace_id,
        agent_id="static_reviewer",
        stage_key="review",
        status=RunStatus.RUNNING,
    )
    db_session.add(fresh)
    db_session.commit()

    with pytest.raises(FilingRefused, match="waiting for triage"):
        check_filing_budget(db_session, fresh)


def test_an_untriaged_finding_that_ran_is_reported(db_session, family):
    parent, _, run = family
    finding = _file(db_session, run, "A finding", parent=parent)
    assert untriaged_discoveries_that_ran(db_session) == []

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

    (violation,) = untriaged_discoveries_that_ran(db_session)
    assert violation.subject_id == finding.id
