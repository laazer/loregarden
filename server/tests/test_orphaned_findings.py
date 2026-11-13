"""A finding outlives the source that was supposed to decide it.

`scope_round.round_due` asks for a scope decision when the source enters a
gate or terminal stage. A source that finishes without crossing one again can
never be asked, so its findings wait forever and every dispatcher refuses them
(`lg-milestone-that-2606`).
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    AgentRun,
    FindingDecision,
    RunStatus,
    TicketDiscovery,
    TicketState,
    utcnow,
)
from loregarden.services.discovered_work import undecided
from loregarden.services.orphaned_findings import (
    defer_findings_whose_source_finished,
    findings_whose_source_finished,
)
from loregarden.services.seed import seed_database
from loregarden.testing.factories import make_workspace_ticket
from sqlmodel import Session


@pytest.fixture
def seeded(db_session: Session) -> Session:
    seed_database(db_session)
    return db_session


def _file(session: Session, source, filed) -> TicketDiscovery:
    """Record ``filed`` as a finding a run on ``source`` filed, undecided."""
    run = AgentRun(
        run_code=f"run_{source.external_id}",
        ticket_id=source.id,
        workspace_id=source.workspace_id,
        agent_id="static_reviewer",
        stage_key="review",
        status=RunStatus.SUCCEEDED,
    )
    session.add(run)
    session.commit()
    row = TicketDiscovery(
        filed_ticket_id=filed.id,
        source_ticket_id=source.id,
        agent_run_id=run.id,
        filed_at=utcnow(),
    )
    session.add(row)
    session.commit()
    return row


def test_a_finding_whose_source_finished_is_detected(seeded: Session):
    source = make_workspace_ticket(seeded, "of-source-1", state=TicketState.DONE)
    source.resolved_at = utcnow()
    filed = make_workspace_ticket(seeded, "of-filed-1", state=TicketState.BACKLOG)
    _file(seeded, source, filed)

    violations = findings_whose_source_finished(seeded)

    assert [v.ticket_id for v in violations] == [filed.id]
    assert "of-source-1" in violations[0].detail
    assert violations[0].entered_at == source.resolved_at


def test_a_source_still_open_is_not_a_violation(seeded: Session):
    source = make_workspace_ticket(seeded, "of-source-2", state=TicketState.IN_PROGRESS)
    filed = make_workspace_ticket(seeded, "of-filed-2", state=TicketState.BACKLOG)
    _file(seeded, source, filed)

    assert findings_whose_source_finished(seeded) == []


def test_a_decided_finding_is_not_a_violation(seeded: Session):
    source = make_workspace_ticket(seeded, "of-source-3", state=TicketState.DONE)
    filed = make_workspace_ticket(seeded, "of-filed-3", state=TicketState.BACKLOG)
    row = _file(seeded, source, filed)
    row.decision = FindingDecision.FOLLOW_UP
    seeded.add(row)
    seeded.commit()

    assert findings_whose_source_finished(seeded) == []


def test_a_finished_finding_needs_no_decision(seeded: Session):
    """Nothing is refused: a closed ticket is started by no dispatcher."""
    source = make_workspace_ticket(seeded, "of-source-4", state=TicketState.DONE)
    filed = make_workspace_ticket(seeded, "of-filed-4", state=TicketState.WONT_DO)
    _file(seeded, source, filed)

    assert findings_whose_source_finished(seeded) == []


def test_the_repair_defers_it_and_converges(seeded: Session):
    source = make_workspace_ticket(seeded, "of-source-5", state=TicketState.DONE)
    filed = make_workspace_ticket(seeded, "of-filed-5", state=TicketState.BACKLOG)
    _file(seeded, source, filed)
    violations = findings_whose_source_finished(seeded)

    assert defer_findings_whose_source_finished(seeded, violations) == 1

    seeded.refresh(filed)
    assert undecided(seeded, filed.id) is None, "the dispatcher's refusal is cleared"
    assert filed.state is TicketState.BACKLOG, "deferring does not close the ticket"
    row = seeded.get(TicketDiscovery, _only_discovery(seeded, filed.id))
    assert row.decision is FindingDecision.DEFER
    assert row.decided_by == "control_plane"
    assert findings_whose_source_finished(seeded) == [], "a second pass finds nothing"


def test_the_repair_skips_a_finding_decided_between_detect_and_repair(seeded: Session):
    source = make_workspace_ticket(seeded, "of-source-6", state=TicketState.DONE)
    filed = make_workspace_ticket(seeded, "of-filed-6", state=TicketState.BACKLOG)
    row = _file(seeded, source, filed)
    violations = findings_whose_source_finished(seeded)
    row.decision = FindingDecision.FOLLOW_UP
    seeded.add(row)
    seeded.commit()

    assert defer_findings_whose_source_finished(seeded, violations) == 0
    seeded.refresh(row)
    assert row.decision is FindingDecision.FOLLOW_UP, "the real decision stands"


def _only_discovery(session: Session, filed_id: str) -> str:
    from sqlmodel import col, select

    return session.exec(
        select(TicketDiscovery.id).where(col(TicketDiscovery.filed_ticket_id) == filed_id)
    ).one()
