"""The discovery backfill recovers each filing's direction and triage from what was recorded.

Until `ticket_discoveries`, a run's filing was one symmetric relation stamped
``run:<id>``, stored sorted, so which side was filed is recovered from the
run's own ticket. Closed findings, and open ones other work already depends
on, are recorded decided; every other open one is undecided.
"""

from __future__ import annotations

import pytest
from loregarden.db.versions.ticket_discoveries import m_ticket_discoveries
from loregarden.models.domain import (
    AgentRun,
    FindingDecision,
    RunStatus,
    Ticket,
    TicketDependency,
    TicketDiscovery,
    TicketRelation,
    TicketState,
)
from loregarden.services.seed import seed_database
from sqlalchemy import text
from sqlmodel import Session, select
from tests.factories import make_workspace_ticket


def _filed(session: Session, run: AgentRun, external_id: str, state: TicketState) -> Ticket:
    """A ticket ``run`` filed, recorded the old way: a sorted relation, no discovery row."""
    ticket = make_workspace_ticket(session, external_id, state=state)
    low, high = sorted([run.ticket_id, ticket.id])
    session.add(TicketRelation(ticket_id=low, related_ticket_id=high, created_by=f"run:{run.id}"))
    session.commit()
    return ticket


@pytest.fixture(name="filings")
def filings_fixture(db_session: Session) -> dict[str, Ticket]:
    seed_database(db_session)
    source = make_workspace_ticket(db_session, "bf-source")
    run = AgentRun(
        run_code="run_bf",
        ticket_id=source.id,
        workspace_id=source.workspace_id,
        agent_id="architecture_reviewer",
        stage_key="review",
        status=RunStatus.SUCCEEDED,
    )
    db_session.add(run)
    db_session.commit()
    tickets = {
        "source": source,
        "open": _filed(db_session, run, "bf-open", TicketState.BACKLOG),
        "closed": _filed(db_session, run, "bf-closed", TicketState.WONT_DO),
        "depended": _filed(db_session, run, "bf-depended", TicketState.BACKLOG),
    }
    dependent = make_workspace_ticket(db_session, "bf-dependent")
    db_session.add(
        TicketDependency(ticket_id=dependent.id, depends_on_ticket_id=tickets["depended"].id)
    )
    db_session.commit()
    return tickets


def _backfill(session: Session) -> dict[str, TicketDiscovery]:
    """Run the migration; its rows by the filed ticket's external id."""
    m_ticket_discoveries(session.connection())
    session.commit()
    names = {t.id: t.external_id for t in session.exec(select(Ticket)).all()}
    return {names[r.filed_ticket_id]: r for r in session.exec(select(TicketDiscovery)).all()}


def test_each_filing_is_recorded_once_from_its_source(db_session, filings):
    rows = _backfill(db_session)
    assert set(rows) == {"bf-open", "bf-closed", "bf-depended"}
    assert {row.source_ticket_id for row in rows.values()} == {filings["source"].id}


def test_only_an_open_finding_nothing_depends_on_starts_undecided(db_session, filings):
    rows = _backfill(db_session)
    assert rows["bf-open"].decision is None
    assert rows["bf-closed"].decision is FindingDecision.DROP
    assert rows["bf-depended"].decision is FindingDecision.FOLLOW_UP
    assert {rows["bf-closed"].decided_by, rows["bf-depended"].decided_by} == {"backfill"}


def test_a_rerun_adds_nothing(db_session, filings):
    _backfill(db_session)
    _backfill(db_session)
    assert len(db_session.exec(select(TicketDiscovery)).all()) == 3


def test_it_builds_the_table_where_there_is_none(db_session, filings):
    db_session.connection().execute(text("DROP TABLE ticket_discoveries"))
    db_session.commit()
    assert len(_backfill(db_session)) == 3
