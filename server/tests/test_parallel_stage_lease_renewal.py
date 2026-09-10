"""A parallel stage member's lease is renewed for as long as it is executing.

The single-agent stage has been renewed since the lease existed; the parallel
members were not, and `run_has_renewer` does not cover them — it asks whether
the run is externally harnessed, and these runs are not, so
`agent_run_lease_expired` judged them against `started_at` and never moved off
it. Every review lens that outran AGENT_RUN_LEASE was reaped by the
reconciliation sweep while its agent was still working, on an idle box: with
zero renewals the expiry is load-independent and lands on schedule.

The assertion has to be taken *during* execution. `lease_renewal` stops
stamping when the body returns, so a check afterwards proves only that a
renewal happened at some point, and would pass against a renewer that fired
once and died.
"""

from __future__ import annotations

import time
from datetime import datetime, timedelta, timezone

import pytest
from loregarden.agents.executors.cli import CliAgentExecutor
from loregarden.models.domain import (
    AgentRun,
    RunStatus,
    WorkItemType,
)
from loregarden.services.builtin_orchestrator import _run_and_collect_parallel_results
from loregarden.services.run_lease import AGENT_RUN_LEASE, agent_run_lease_expired
from loregarden.services.ticket_service import TicketService
from sqlmodel import Session

LENSES = ("static_qa", "architecture_reviewer", "gdscript_reviewer")


@pytest.fixture(name="lens_runs")
def lens_runs_fixture(db_session: Session) -> list[AgentRun]:
    """Three RUNNING members of a parallel stage, started longer ago than the
    lease — the state a review lens is in when the sweep looks at it."""
    ticket = TicketService(db_session).create_ticket(
        workspace_slug="loregarden",
        title="parallel lease renewal",
        work_item_type=WorkItemType.MILESTONE,
    )
    stale = datetime.now(timezone.utc) - (AGENT_RUN_LEASE + timedelta(minutes=1))
    runs = []
    for lens in LENSES:
        run = AgentRun(
            run_code=f"parallel_lease_{lens}",
            ticket_id=ticket.id,
            workspace_id=ticket.workspace_id,
            agent_id=lens,
            status=RunStatus.RUNNING,
        )
        run.started_at = stale
        db_session.add(run)
        runs.append(run)
    db_session.commit()
    for run in runs:
        db_session.refresh(run)
    return runs


def test_member_lease_is_renewed_while_it_executes(
    db_session: Session, lens_runs: list[AgentRun], isolated_db
) -> None:
    still_expired: dict[str, bool] = {}

    def _fake_execute(self, run, ticket, **kwargs):
        # Read on a fresh session: the renewer commits on its own connection,
        # so the executor's session would not see the stamp. Polled rather
        # than read once, because `lease_renewal` starts its thread and yields
        # immediately — the first stamp lands just after the body begins.
        deadline = time.monotonic() + 10.0
        expired = True
        while time.monotonic() < deadline:
            with Session(isolated_db) as probe:
                observed = probe.get(AgentRun, run.id)
                assert observed is not None
                expired = agent_run_lease_expired(probe, observed)
            if not expired:
                break
            time.sleep(0.05)
        still_expired[run.agent_id] = expired
        run.status = RunStatus.SUCCEEDED
        run.output = (
            "<<<LOREGARDEN_STAGE_REPORT>>>\n"
            '{"status": "pass", "confidence": 0.95}\n'
            "<<<END_STAGE_REPORT>>>\n"
        )
        return run

    with pytest.MonkeyPatch.context() as mp:
        mp.setattr(CliAgentExecutor, "execute", _fake_execute)
        _run_and_collect_parallel_results(lens_runs)

    assert set(still_expired) == set(LENSES)
    assert not any(still_expired.values()), (
        "a parallel member stayed past its lease for the whole time it was "
        "executing, so the reconciliation sweep would reap it mid-stage: "
        f"{sorted(k for k, v in still_expired.items() if v)}"
    )
