"""An external harness renews its lane by talking to the control plane.

431 gave a lane's occupant a lease it has to renew, so an abandoned run stops
holding a lane forever. `orchestration_lease_expired` renews on stage
boundaries and, separately, treats a live agent run beneath the orchestration as
liveness outright.

The external-harness protocol (#155) uses neither. It drives stages through
`begin_external_stage` / `finish_external_stage`, not the `start_stage` /
`complete_stage` callbacks that stamp `last_seen_at` — so an external run's
`last_seen_at` was never written at all, and its lane survived only for as long
as a stage happened to be checked out. Between two stages there is no live agent
run, and the fallback is `started_at`: a harness past the lease got its lane
reclaimed and its orchestration failed in the gap between finishing one stage
and asking for the next.

That is the exact failure 431 was written to fix, arriving through the one
driver the lease was supposed to be *for* — a harness in someone else's
terminal has no other way to say it is alive.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    OrchestrationRunStatus,
    Ticket,
    WorkItemType,
)
from loregarden.services.external_harness import (
    begin_external_stage,
    finish_external_stage,
    start_external_orchestration,
)
from loregarden.services.run_concurrency import orchestration_lease_expired
from loregarden.services.run_service import settle_expired_orchestration_leases
from loregarden.services.ticket_service import TicketService


@pytest.fixture(name="ticket")
def ticket_fixture(db_session) -> Ticket:
    return TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="Driven from someone else's terminal",
        work_item_type=WorkItemType.MILESTONE,
    )


def _age(run, minutes: int) -> None:
    """Push every liveness stamp back, as a long-running harness would."""
    stale = datetime.now(timezone.utc) - timedelta(minutes=minutes)
    run.created_at = stale
    run.started_at = stale
    if run.last_seen_at:
        run.last_seen_at = stale


def test_a_stage_boundary_renews_the_lease(db_session, ticket):
    """Checking a stage out is the harness saying it is alive.

    Without this the run's only stamp is `started_at`, so a harness that has
    been working steadily for longer than the lease is indistinguishable from
    one whose operator closed the terminal an hour ago.
    """
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    _age(orch_run, minutes=45)
    db_session.add(orch_run)
    db_session.commit()

    begin_external_stage(db_session, orch_run)
    db_session.refresh(orch_run)

    assert orch_run.last_seen_at is not None
    assert not orchestration_lease_expired(db_session, orch_run)


def test_the_gap_between_two_stages_does_not_lose_the_lane(db_session, ticket):
    """The window the live-agent-run rule cannot cover.

    While a stage is checked out an agent run is in flight, which outranks the
    lease. The moment it is settled that protection is gone, and the harness is
    about to ask for the next stage. A sweep landing in that window used to
    fail the orchestration and hand the lane away underneath a harness that was
    still working.
    """
    orch_run = start_external_orchestration(db_session, ticket, harness=ExternalHarness.CLAUDE_CODE)
    _age(orch_run, minutes=45)
    db_session.add(orch_run)
    db_session.commit()

    stage = begin_external_stage(db_session, orch_run)
    assert stage.runs, "expected a real stage to be checked out"

    run = db_session.get(AgentRun, stage.runs[0].agent_run_id)
    finish_external_stage(db_session, run, transcript="done")
    db_session.refresh(orch_run)

    settled = settle_expired_orchestration_leases(db_session)

    assert orch_run.id not in {r.id for r in settled}
    assert orch_run.status != OrchestrationRunStatus.FAILED
