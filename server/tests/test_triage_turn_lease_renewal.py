"""A triage turn renews its lease while it thinks.

Seven of the ten agent-run lease expiries in the fortnight before this were
Baxter turns with `last_seen_at` never stamped: the triage path ran the
executor with no renewer, so any answer longer than AGENT_RUN_LEASE was
reaped mid-turn by the reconciliation sweep and reported as a failed run.
Same test shape as the parallel-member renewal, for the same reason.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    CapacityPool,
    CliAdapter,
    DockerLease,
    RunStatus,
    WorkItemType,
)
from loregarden.services.run_lease import AGENT_RUN_LEASE, agent_run_lease_expired
from loregarden.services.ticket_service import TicketService
from loregarden.services.triage_run_service import (
    TriageTurnExecutor,
    execute_triage_turn_background,
)
from sqlmodel import Session, select
from tests.test_capacity_host_pool import _reserve, _set_ceiling


@pytest.fixture(name="stale_turn")
def stale_turn_fixture(db_session: Session) -> AgentRun:
    ticket = TicketService(db_session).create_ticket(
        workspace_slug="loregarden", title="triage lease", work_item_type=WorkItemType.MILESTONE
    )
    run = AgentRun(
        run_code="triage_lease_turn",
        ticket_id=ticket.id,
        workspace_id=ticket.workspace_id,
        agent_id="triage",
        stage_key="triage",
        status=RunStatus.QUEUED,
    )
    run.started_at = datetime.now(timezone.utc) - (AGENT_RUN_LEASE + timedelta(minutes=1))
    db_session.add(run)
    db_session.commit()
    db_session.refresh(run)
    return run


def test_a_triage_turn_is_renewed_while_it_executes(
    db_session: Session, stale_turn: AgentRun, isolated_db
) -> None:
    observed: dict[str, bool] = {}

    def _fake_execute(self, run, ticket, **kwargs):
        # The turn resets `started_at` when it begins, so a fresh run is never
        # expired at first; the evidence that a renewer exists is the stamp
        # itself, which only the renewer writes. Polled: the beat thread starts
        # just as the body begins.
        deadline = time.monotonic() + 10.0
        stamped = False
        while time.monotonic() < deadline:
            with Session(isolated_db) as probe:
                row = probe.get(AgentRun, run.id)
                assert row is not None
                stamped = row.last_seen_at is not None
                if stamped:
                    assert agent_run_lease_expired(probe, row) is False
            if stamped:
                break
            time.sleep(0.05)
        observed["stamped"] = stamped
        run.status = RunStatus.SUCCEEDED
        return run

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TriageTurnExecutor, "execute", _fake_execute)
        execute_triage_turn_background(stale_turn.id)

    assert observed["stamped"] is True, (
        "nothing renewed the triage turn's lease while it executed, so a turn longer "
        "than AGENT_RUN_LEASE would be reaped mid-answer by the reconciliation sweep"
    )


def test_a_triage_turn_does_not_queue_behind_a_full_machine(
    db_session: Session, stale_turn: AgentRun, monkeypatch
) -> None:
    """A person is waiting on the turn: 7 of 93 sat over 5 minutes behind
    pre-push suites in the month to 2026-11-13 (lg-machine-resource-2621)."""
    monkeypatch.setenv("LOREGARDEN_CLI_ADAPTER", CliAdapter.CLAUDE.value)
    monkeypatch.setattr(settings, "agent_run_capacity_wait_seconds", 0.3)
    _set_ceiling(db_session, CapacityPool.HOST, cpus=4, memory_mb=8192, leases=4)
    assert _reserve(db_session, CapacityPool.HOST, label="pre-push", cpus=4).granted
    ran: list[str] = []

    def _fake_execute(self, run, ticket, **kwargs):
        ran.append(run.id)
        run.status = RunStatus.SUCCEEDED
        return run

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(TriageTurnExecutor, "execute", _fake_execute)
        execute_triage_turn_background(stale_turn.id)

    assert ran == [stale_turn.id]
    db_session.expire_all()
    leases = db_session.exec(
        select(DockerLease).where(DockerLease.agent_run_id == stale_turn.id)
    ).all()
    assert leases == []
