"""An external-harness stage outlives the builtin sweep window (625).

Four review runs were failed mid-analysis on 2026-09-01, all at 10.4-10.5
minutes against a 10-minute lease, carrying `AGENT_LEASE_EXPIRED_MESSAGE`.
Failing them finalized the stage as a reject, which rerouted the ticket and
reset implement, verify and review to pending — discarding committed,
independently verified work.

The exemption those runs should have had already existed and still does:
`run_lease.run_has_renewer` returns False for an externally-harnessed run, and
`agent_run_lease_expired` returns False for anything with no renewer, because a
harness reports at stage boundaries and silence from something never asked to
speak is not evidence.

**What these tests are for is that the exemption cannot be quietly removed.**
It is one `return False` standing between a ten-minute clock and every external
stage in a milestone whose review rounds run fifteen to twenty-five minutes,
and nothing pinned it. A change that "simplified" `run_has_renewer` would pass
every other suite in the repo.

What could NOT be reproduced, and is stated rather than implied: the original
reap. Against this code an over-lease external run is spared, and one of the
three siblings in the live incident survived 21.2 minutes with row fields
identical to the two that died — which no predicate reading only those fields
can explain. The trigger is unidentified; these tests pin the invariant that
was supposed to hold, and the sweeper now records what it judged on, so a
recurrence is diagnosable from the row rather than from elapsed time.
"""

from datetime import datetime, timedelta, timezone

import pytest
from loregarden.models.domain import (
    AgentRun,
    ExternalHarness,
    OrchestrationDriver,
    OrchestrationRun,
    OrchestrationRunStatus,
    RunStatus,
)
from loregarden.services.run_lease import (
    AGENT_RUN_LEASE,
    agent_run_lease_expired,
    run_has_renewer,
)
from loregarden.services.run_service import (
    settle_expired_agent_runs,
    settle_orphaned_agent_runs,
)
from sqlmodel import Session
from tests.factories import make_ticket, make_workspace

#: Comfortably past the lease, and past the 10.5 minutes the live incident took.
WELL_PAST_THE_LEASE = AGENT_RUN_LEASE + timedelta(minutes=15)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


def _external_run(session, *, age, harness=ExternalHarness.CLAUDE_CODE, pid=None):
    ws = make_workspace(session, slug="proj")
    ticket = make_ticket(session, workspace_id=ws.id, ticket_id="t-1")
    orch = OrchestrationRun(
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="ORCH-1",
        driver=OrchestrationDriver.EXTERNAL_MCP,
        external_harness=harness,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(orch)
    session.commit()
    session.refresh(orch)
    run = AgentRun(
        workspace_id=ws.id,
        ticket_id=ticket.id,
        run_code="RUN-1",
        agent_id="static_qa",
        stage_key="review",
        status=RunStatus.RUNNING,
        started_at=datetime.now(timezone.utc) - age,
        external_harness=harness,
        handoff_pid=pid,
        orchestration_run_id=orch.id,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run, orch


def test_an_external_run_past_the_lease_is_not_expired(session):
    """The predicate itself, on the shape the incident had."""
    run, _ = _external_run(session, age=WELL_PAST_THE_LEASE)

    assert run_has_renewer(run) is False, "an external harness has no lease renewer"
    assert agent_run_lease_expired(session, run) is False


def test_the_sweeper_leaves_an_external_run_in_flight(session):
    """The sweep itself, not just the predicate under it.

    Asserting the predicate alone would pass an implementation whose sweeper
    stopped consulting it.
    """
    run, _ = _external_run(session, age=WELL_PAST_THE_LEASE)

    assert settle_expired_agent_runs(session) == []

    session.refresh(run)
    assert run.status == RunStatus.RUNNING
    assert run.finished_at is None


def test_a_builtin_run_past_the_lease_is_still_reaped(session):
    """The discriminator, without which the exemption could be "spare everything".

    A run with a renewer that has gone quiet is exactly what the lease exists to
    disprove, and a test suite that only asserted "nothing is reaped" would be
    satisfied by deleting the sweeper.
    """
    run, _ = _external_run(session, age=WELL_PAST_THE_LEASE, harness=None)

    assert run_has_renewer(run) is True
    reaped = settle_expired_agent_runs(session)

    assert [r.id for r in reaped] == [run.id]


def test_a_dead_recorded_pid_still_settles_an_external_run(session):
    """A pid the machine can see outranks the exemption, both ways.

    The exemption is about a harness this control plane cannot observe. A
    recorded pid that is gone is direct evidence, and sparing that run would
    leave a genuinely dead stage RUNNING forever.
    """
    run, _ = _external_run(session, age=WELL_PAST_THE_LEASE, pid=2147483646)

    assert agent_run_lease_expired(session, run) is True


def test_the_reap_records_what_it_judged_on(session):
    """625 AC3. Three wrong diagnoses came from a row that stated a verdict only.

    The message named the sweep but not its inputs, so checking it meant
    reconstructing elapsed time from two timestamps and guessing the lease.
    """
    run, _ = _external_run(session, age=WELL_PAST_THE_LEASE, harness=None)

    settle_expired_agent_runs(session)

    session.refresh(run)
    assert "lease:" in run.stderr, run.stderr
    assert "last renewed:" in run.stderr, run.stderr
    assert "external_harness: none" in run.stderr, run.stderr
    assert "handoff_pid: none" in run.stderr, run.stderr


def test_settling_orphaned_residue_does_not_take_a_stage_verdict(session):
    """625 AC2, on the one path that does reap an external run.

    `settle_orphaned_agent_runs` fails external children of a terminal parent —
    correct, they are residue. It must not advance the workflow while doing it:
    the orchestration already made the ticket's decision, and a second pass at
    the stage is how a reap turns into a reject that resets completed work.
    """
    run, orch = _external_run(session, age=WELL_PAST_THE_LEASE)
    orch.status = OrchestrationRunStatus.FAILED
    session.add(orch)
    session.commit()

    settled = settle_orphaned_agent_runs(session)

    assert [r.id for r in settled] == [run.id]
    session.refresh(run)
    assert run.status == RunStatus.FAILED
    # The residue message, not the lease one: the two reasons stay tellable
    # apart in the row, which is the whole of AC3.
    assert "lease expired" not in (run.stderr or "").lower(), run.stderr
