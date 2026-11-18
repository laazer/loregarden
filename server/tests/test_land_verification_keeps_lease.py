"""A land verification keeps its orchestration alive while it runs.

The terminal stage's landing runs the configured suite on the driver's own
thread, and the lease is otherwise renewed only at stage boundaries. On
2026-11-17 lg-code-knowledge-1055's landing waited for capacity and then ran
its suite for over `ORCHESTRATION_LEASE`; the sweep settled `orch_52b935` as
FAILED ("driver is gone") at 22:00:37 while pytest was 17% through. These pin
that the lease is renewed for the whole verification, and only for a landing
an orchestration drives.
"""

from __future__ import annotations

from datetime import timedelta
from unittest import mock

import pytest
from loregarden.models.domain import OrchestrationRun, OrchestrationRunStatus, utcnow
from loregarden.services import capacity_line
from loregarden.services.land_ticket import land_ticket
from loregarden.services.run_concurrency import ORCHESTRATION_LEASE, orchestration_lease_expired
from loregarden.testing.factories import make_orchestration_run
from sqlmodel import Session
from tests import land_receipt_helpers as helpers

#: Longer than several keep-alive ticks at the shortened interval below.
SLOW_SUITE = "python3 -c 'import time; time.sleep(1.5)'"
TICK_SECONDS = 0.2


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="ticking")
def ticking_fixture():
    """Count lease renewals, at an interval a test can wait out."""
    renew = mock.Mock(wraps=capacity_line.renew_orchestration_lease)
    with (
        mock.patch.object(capacity_line, "KEEP_ALIVE_INTERVAL_SECONDS", TICK_SECONDS),
        mock.patch.object(capacity_line, "renew_orchestration_lease", renew),
    ):
        yield renew


def _stale_run(session: Session, landing: helpers.Landing) -> OrchestrationRun:
    """A RUNNING orchestration last seen longer ago than the lease allows."""
    run = make_orchestration_run(
        session, workspace_id=landing.workspace.id, ticket_id=landing.ticket.id
    )
    run.status = OrchestrationRunStatus.RUNNING
    run.started_at = utcnow() - ORCHESTRATION_LEASE * 2
    run.last_seen_at = utcnow() - ORCHESTRATION_LEASE - timedelta(minutes=1)
    session.add(run)
    session.commit()
    assert orchestration_lease_expired(session, run), "the fixture must start expired"
    return run


def test_the_lease_is_renewed_throughout_the_suite_not_only_at_its_start(
    session, tmp_path, ticking
):
    landing = helpers.build(session, tmp_path, "keepalive", commands=[SLOW_SUITE])
    run = _stale_run(session, landing)

    result = land_ticket(session, landing.ticket, landing.workspace)

    assert result.ok, result.detail
    ids = {call.args[1] for call in ticking.call_args_list}
    assert ids == {run.id}, f"renewed {ids}, not the driving run {run.id}"
    # One renewal on entry, then one per tick while the 1.5s suite runs.
    assert ticking.call_count >= 4, (
        f"{ticking.call_count} renewal(s) across a suite longer than several ticks: "
        "the lease is renewed only once, so a long suite still outlives it"
    )
    session.refresh(run)
    assert not orchestration_lease_expired(session, run), (
        f"last seen {run.last_seen_at}: the sweep would fail this run as abandoned"
    )


def test_a_landing_no_orchestration_drives_renews_nothing(session, tmp_path, ticking):
    """`loregarden_land_ticket` and the CLI land with no run; there is no lease."""
    landing = helpers.build(session, tmp_path, "nodriver", commands=[SLOW_SUITE])

    result = land_ticket(session, landing.ticket, landing.workspace)

    assert result.ok, result.detail
    assert ticking.call_count == 0, f"renewed {ticking.call_args_list} with no run to renew"


def test_a_finished_run_is_not_kept_alive(session, tmp_path, ticking):
    """Only a RUNNING orchestration is the driver; a settled one stays settled."""
    landing = helpers.build(session, tmp_path, "settled", commands=[SLOW_SUITE])
    run = _stale_run(session, landing)
    run.status = OrchestrationRunStatus.FAILED
    session.add(run)
    session.commit()

    land_ticket(session, landing.ticket, landing.workspace)

    assert ticking.call_count == 0, f"renewed a {run.status} run: {ticking.call_args_list}"


def test_a_failed_renewal_is_logged_and_the_next_tick_retries(session):
    """A tick that cannot write is retryable: logged, not raised into the suite."""
    broken = mock.Mock(side_effect=capacity_line.SQLAlchemyError("database is locked"))
    keep = capacity_line.OrchestrationLeaseKeepAlive(broken, "orch-x", interval=TICK_SECONDS)

    with mock.patch.object(capacity_line.logger, "exception") as logged:
        keep.renew()
        keep.renew()

    assert logged.call_count == 2, "each failed tick must be reported, not swallowed"
    assert "orch-x" in logged.call_args.args
