"""A run's filing goes beside its source, atomically, and its scope decision can be corrected.

Filed under the ticket its run was working on, a finding made that ticket a
parent, and a parent never runs its own stages: lg-milestone-that-1420 sat at
verify with 1431 under it, and a `follow_up` could not move it, since a bug
holds only tasks and no parent of a bug holds a task (lg-milestone-that-2604).
A filing whose dependency was refused committed the ticket first and never
wrote its finding row, so lg-milestone-that-2596 was an ordinary child of
1429 (lg-milestone-that-2605).
"""

from __future__ import annotations

import json
from datetime import timedelta
from unittest.mock import patch

import pytest
from loregarden.mcp import tools
from loregarden.mcp.discovery_tools import decide_finding_tool
from loregarden.mcp.tools import execute_tool, normalize_tool_arguments
from loregarden.models.domain import (
    AgentRun,
    FindingDecision,
    Ticket,
    TicketDiscovery,
    TicketState,
    WorkItemType,
    utcnow,
)
from loregarden.services.discovered_work import has_owned_children, undecided
from loregarden.services.finding_placement_checks import (
    follow_ups_under_their_source,
    unrecorded_children_of_a_run,
)
from loregarden.services.scope_round import decide_finding
from sqlmodel import Session, func, select
from tests.test_discovered_work import family_fixture  # noqa: F401 - fixture used by name


def _create(session: Session, run: AgentRun, *, parent: Ticket, kind: str, **extra) -> dict:
    return json.loads(
        execute_tool(
            session,
            "loregarden_create_ticket",
            normalize_tool_arguments(
                "loregarden_create_ticket",
                {
                    "workspace_slug": "loregarden",
                    "title": "A finding",
                    "work_item_type": kind,
                    "parent": parent.id,
                    **extra,
                },
            ),
            orchestrated=True,
            run_id=run.id,
        )
    )


def _tickets(session: Session) -> int:
    return session.exec(select(func.count()).select_from(Ticket)).one()


def _under_its_source(session: Session, source: Ticket, run: AgentRun) -> Ticket:
    """A finding filed the old way: under its source, with its row."""
    finding = Ticket(
        external_id=f"{source.external_id}-f",
        workspace_id=source.workspace_id,
        title="Filed under its source",
        parent_ticket_id=source.id,
        work_item_type=WorkItemType.BUG,
    )
    session.add(finding)
    session.commit()
    session.add(
        TicketDiscovery(filed_ticket_id=finding.id, source_ticket_id=source.id, agent_run_id=run.id)
    )
    session.commit()
    session.refresh(finding)
    return finding


def _decide(session, finding, decision, *, revise=False):
    return decide_finding(
        session, finding, decision, reason="test", decided_by="operator", revise=revise
    )


# --- 2605: a run's filing is a finding, or nothing --------------------------------------


def test_no_writer_creates_a_run_child_without_its_finding(db_session, family):
    parent, child, run = family
    before = _tickets(db_session)

    # Under its source: placed beside it, with its row.
    filed = _create(db_session, run, parent=child, kind="bug")
    # A throw while writing the row leaves no ticket behind.
    with (
        patch.object(tools, "record_discovery", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        _create(db_session, run, parent=parent, kind="bug")
    # A refused prerequisite leaves no ticket behind (2596's own call).
    with pytest.raises(ValueError, match="ancestor"):
        _create(db_session, run, parent=parent, kind="bug", depends_on=[parent.id])

    assert _tickets(db_session) == before + 1
    ticket = db_session.get(Ticket, filed["id"])
    assert ticket.parent_ticket_id == parent.id
    assert undecided(db_session, ticket.id).source_ticket_id == child.id
    assert not has_owned_children(db_session, child.id)
    assert unrecorded_children_of_a_run(db_session) == []


def test_a_throw_relating_the_filing_leaves_no_orphan_ticket(db_session, family):
    parent, child, run = family
    with (
        patch.object(tools, "record_filing", side_effect=RuntimeError("boom")),
        pytest.raises(RuntimeError),
    ):
        _create(db_session, run, parent=parent, kind="bug")
    orphans = db_session.exec(
        select(Ticket)
        .outerjoin(TicketDiscovery, TicketDiscovery.filed_ticket_id == Ticket.id)
        .where(Ticket.parent_ticket_id == parent.id, TicketDiscovery.id.is_(None))
    ).all()
    assert [t.external_id for t in orphans] == [child.external_id]


def test_a_finding_of_a_bug_is_filed_beside_it_as_a_kind_its_parent_holds(db_session, family):
    _parent, child, run = family
    bug = Ticket(
        external_id="dw-bug",
        workspace_id=child.workspace_id,
        title="A bug",
        parent_ticket_id=child.id,
        work_item_type=WorkItemType.BUG,
    )
    db_session.add(bug)
    db_session.commit()
    run.ticket_id = bug.id
    db_session.add(run)
    db_session.commit()

    filed = _create(db_session, run, parent=bug, kind="task")

    ticket = db_session.get(Ticket, filed["id"])
    assert (ticket.parent_ticket_id, ticket.work_item_type) == (child.id, WorkItemType.BUG)
    assert "beside its source" in filed["placed"]


def test_a_filing_elsewhere_keeps_the_parent_and_kind_it_asked_for(db_session, family):
    parent, _child, run = family
    filed = _create(db_session, run, parent=parent, kind="feature")
    ticket = db_session.get(Ticket, filed["id"])
    assert (ticket.parent_ticket_id, ticket.work_item_type) == (parent.id, WorkItemType.FEATURE)
    assert "placed" not in filed


# --- 2604: follow_up moves it out; a decision can be corrected ---------------------------


def test_no_writer_leaves_a_follow_up_under_its_source(db_session, family):
    parent, child, run = family
    finding = _under_its_source(db_session, child, run)

    _decide(db_session, finding, FindingDecision.FOLLOW_UP)

    db_session.refresh(finding)
    assert finding.parent_ticket_id == parent.id
    assert not has_owned_children(db_session, child.id)
    assert follow_ups_under_their_source(db_session) == []


def test_a_follow_up_its_type_cannot_carry_up_is_refused_naming_the_others(db_session, family):
    _parent, child, run = family
    finding = _under_its_source(db_session, child, run)
    finding.work_item_type = WorkItemType.CAPABILITY  # a milestone holds no capability
    db_session.add(finding)
    db_session.commit()

    with pytest.raises(ValueError, match="amend, defer or drop"):
        _decide(db_session, finding, FindingDecision.FOLLOW_UP)
    assert undecided(db_session, finding.id) is not None


def test_a_follow_up_of_a_source_with_no_parent_is_refused(db_session, family):
    parent, _child, run = family
    finding = _under_its_source(db_session, parent, run)
    with pytest.raises(ValueError, match="no parent to own"):
        _decide(db_session, finding, FindingDecision.FOLLOW_UP)


def test_the_detector_sees_a_follow_up_left_under_its_source(db_session, family):
    _parent, child, run = family
    finding = _under_its_source(db_session, child, run)
    row = undecided(db_session, finding.id)
    row.decision = FindingDecision.FOLLOW_UP
    db_session.add(row)
    db_session.commit()
    [violation] = follow_ups_under_their_source(db_session)
    assert violation.subject_id == finding.id


def test_the_detector_sees_a_child_created_during_a_run_with_no_row(db_session, family):
    _parent, child, run = family
    run.started_at = utcnow() - timedelta(minutes=1)
    db_session.add(run)
    db_session.commit()
    stray = Ticket(
        external_id="dw-stray",
        workspace_id=child.workspace_id,
        title="Created by hand during the run",
        parent_ticket_id=child.id,
        work_item_type=WorkItemType.CAPABILITY,
        created_at=utcnow(),
    )
    db_session.add(stray)
    db_session.commit()
    [violation] = unrecorded_children_of_a_run(db_session)
    assert violation.subject_id == stray.id


def test_a_dropped_finding_revised_to_follow_up_reopens_and_keeps_its_history(db_session, family):
    parent, _child, run = family
    ticket = db_session.get(Ticket, _create(db_session, run, parent=parent, kind="bug")["id"])
    _decide(db_session, ticket, FindingDecision.DROP)
    db_session.refresh(ticket)
    assert ticket.state is TicketState.WONT_DO

    with pytest.raises(ValueError, match="revise=true"):
        _decide(db_session, ticket, FindingDecision.FOLLOW_UP)
    row = _decide(db_session, ticket, FindingDecision.FOLLOW_UP, revise=True)

    db_session.refresh(ticket)
    assert ticket.state is not TicketState.WONT_DO and not ticket.state_locked
    assert row.decision is FindingDecision.FOLLOW_UP
    assert "revised from drop by operator" in row.decision_reason


def test_an_amend_cannot_be_revised(db_session, family):
    parent, _child, run = family
    ticket = db_session.get(Ticket, _create(db_session, run, parent=parent, kind="bug")["id"])
    decide_finding(
        db_session,
        ticket,
        FindingDecision.AMEND,
        reason="test",
        decided_by="operator",
        criterion="It holds.",
    )
    with pytest.raises(ValueError, match="folded into its source"):
        _decide(db_session, ticket, FindingDecision.DEFER, revise=True)


def test_a_spec_run_cannot_revise_a_decision(db_session, family):
    parent, _child, run = family
    ticket = db_session.get(Ticket, _create(db_session, run, parent=parent, kind="bug")["id"])
    _decide(db_session, ticket, FindingDecision.DEFER)
    run.agent_id = "spec"
    db_session.add(run)
    db_session.commit()
    with pytest.raises(ValueError, match="still waiting"):
        decide_finding_tool(
            db_session,
            {"ticket_id": ticket.id, "decision": "drop", "reason": "r", "revise": True},
            orchestrated=True,
            run_id=run.id,
        )
