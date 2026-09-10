"""The per-stage opt-in: who takes a lease, who does not, and what it costs them.

The property that makes the opt-in worth having is the negative one. Most stages
never start a container, and a lease held by a planning stage is capacity taken
from the test stage waiting behind it — so a stage that declares nothing must
touch the ledger not at all, not "briefly". These tests assert on the ledger
being untouched rather than on the reservation being None, because those are
different claims and only the first one matters.

The lookup-failure cases are here for the opposite reason: a stage whose
template cannot be resolved must still run. Turning a lookup problem into a
refused dispatch would be a worse outage than the over-subscription this
prevents.
"""

from __future__ import annotations

import pytest
from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseStatus,
    WorkflowStageDef,
)
from loregarden.services import docker_leases
from loregarden.services.docker_leases import (
    DockerCapacityUnavailable,
    docker_capacity_for_stage,
)
from loregarden.services.stage_docker_capacity import stage_def_for_run, stage_docker_capacity
from sqlmodel import Session, select
from tests.factories import make_agent_run, make_workspace


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="ceiling", autouse=True)
def ceiling_fixture(session):
    pool = docker_leases.load_pool(session)
    pool.ceiling_cpus = 2.0
    pool.ceiling_memory_mb = 4096
    pool.ceiling_leases = 1
    pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(pool)
    session.commit()
    return pool


@pytest.fixture(name="run")
def run_fixture(session):
    workspace = make_workspace(session, slug="stage-docker")
    return make_agent_run(session, workspace_id=workspace.id, stage_key="implement")


def _leases(session) -> list[DockerLease]:
    return list(session.exec(select(DockerLease)).all())


def test_a_stage_declaring_nothing_never_touches_the_ledger(session, run) -> None:
    """Not "takes a lease and releases it" — takes nothing at all. This is the
    whole reason the opt-in is per stage."""
    stage = WorkflowStageDef(key="implement", name="Implement")
    assert stage.docker_footprint is DockerFootprint.NONE

    with docker_capacity_for_stage(session, run, stage) as reservation:
        assert reservation is None
        assert _leases(session) == []
        assert docker_leases.load_pool(session).revision == 0, "the pool was written to"

    assert _leases(session) == []


def test_a_stage_that_declares_a_footprint_holds_it_for_the_block(session, run) -> None:
    stage = WorkflowStageDef(key="e2e", name="End to end", docker_footprint=DockerFootprint.STACK)
    with docker_capacity_for_stage(session, run, stage) as reservation:
        assert reservation is not None and reservation.granted
        assert (reservation.cpus, reservation.memory_mb) == (2.0, 4096)
        held = _leases(session)
        assert len(held) == 1
        assert held[0].status is DockerLeaseStatus.HELD
        assert held[0].agent_run_id == run.id, (
            "the lease must name its run, or the reaper cannot inherit that run's heartbeat"
        )

    session.expire_all()
    assert _leases(session)[0].status is DockerLeaseStatus.RELEASED
    assert docker_leases.load_pool(session).held_count == 0


def test_the_lease_is_released_even_when_the_stage_raises(session, run) -> None:
    """A failed stage that kept its capacity would take the pool down with it,
    one failure at a time."""
    stage = WorkflowStageDef(key="e2e", name="E2E", docker_footprint=DockerFootprint.STACK)
    with pytest.raises(RuntimeError, match="stage blew up"):
        with docker_capacity_for_stage(session, run, stage):
            raise RuntimeError("stage blew up")

    session.expire_all()
    assert docker_leases.load_pool(session).held_count == 0


def test_a_stage_that_cannot_get_capacity_fails_rather_than_running_anyway(session, run) -> None:
    """A ledger a caller may ignore when it is inconvenient is a report, not a
    limit — so the stage fails, and it fails saying what it wanted."""
    blocker = docker_leases.reserve(
        session,
        holder_label="somebody else",
        footprint=DockerFootprint.STACK,
    )
    assert blocker.granted

    stage = WorkflowStageDef(key="e2e", name="E2E", docker_footprint=DockerFootprint.STACK)
    with pytest.raises(DockerCapacityUnavailable, match="stack"):
        with docker_capacity_for_stage(session, run, stage, wait_seconds=0.3):
            pytest.fail("the stage body must not run without capacity")


def test_a_refused_stage_does_not_leave_a_waiter_wedging_the_queue(session, run) -> None:
    """Strict head-of-line means an abandoned waiter blocks everything behind
    it. A stage that gave up must take its place in line with it."""
    docker_leases.reserve(session, holder_label="blocker", footprint=DockerFootprint.STACK)
    stage = WorkflowStageDef(key="e2e", name="E2E", docker_footprint=DockerFootprint.STACK)

    with pytest.raises(DockerCapacityUnavailable):
        with docker_capacity_for_stage(session, run, stage, wait_seconds=0.3):
            pass

    session.expire_all()
    waiting = [lease for lease in _leases(session) if lease.status is DockerLeaseStatus.WAITING]
    assert waiting == [], "the abandoned claim is still at the head of the queue"


def test_an_unresolvable_stage_runs_without_a_lease_rather_than_failing(session, run) -> None:
    """A run whose ticket has no workflow instance. Refusing to run it would turn
    a lookup problem into an outage, and the common answer is NONE anyway."""
    assert stage_def_for_run(session, run) is None

    with stage_docker_capacity(session, run) as reservation:
        assert reservation is None
    assert _leases(session) == []
