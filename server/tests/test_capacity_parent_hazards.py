"""Adversarial cases for the run-scoped parent lease (lg-machine-resource-1923).

The designed tests cover the paths the ticket describes. These cover the ones a
plausible implementation gets wrong while passing all of them. Every case here
is an invariant the ticket already asserts somewhere in prose, driven from a
corner rather than from the middle:

**A parent that is itself a child** (R1 + R8c). A nested child-ticket run's
parent carries `parent_lease_id`, and `held_for_run`'s reuse lookup filters
`parent_lease_id IS NULL`. So the obvious `acquire_or_find_parent` — reserve,
let the dedupe inside `reserve` answer — is idempotent for a top-level run and
NOT idempotent for a nested one: the second call books a second standing claim
under the same outer parent, and the subtree's bounded cost stops being
bounded. R1 says "exactly one", and nesting is where "exactly" is hard.

**Two claimants, one run.** `reserve`'s dedupe is a read followed by an insert
in a later transaction, with no unique constraint behind it. Two processes that
both reach a transition for the same run — a server and a CLI, an orchestrator
and a resumed one — can both find nothing held.

**Giving it back.** `release_for_park` and `release_parent` must cascade to a
live gate child (a park can land while a transition is mid-flight) and must
credit the pool exactly once however many times they are called. A lease
credited twice lets admission run past its own ceiling, which is the quiet
direction of wrong.

**A parent that is gone.** A gate handed a `parent_lease_id` that has been
released must be refused, never silently promoted to the top-level claim it
would have made before this ticket. That fallback would restore the 29-minute
wait the ticket exists to remove, with nothing on any surface saying so.

**Cover returned per transition.** A LIGHT parent lends 0.5 cpu to each SERVICE
gate child. If a released child's cover is not returned to
`parent.child_covered_cpus`, the parent is fully lent after the first
transition and every later gate is refused `exceeds_parent_grant` — a run that
works once and then cannot transition again, which no two-transition test sees.
"""

from __future__ import annotations

import threading
from uuid import uuid4

import pytest
from loregarden.models.domain import (
    AgentRun,
    CapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    GateOutcome,
    OrchestrationRun,
    RunStatus,
    Ticket,
    TicketState,
    Workspace,
)
from loregarden.services import orchestration_capacity
from loregarden.services.capacity_children import reserve_child
from loregarden.services.docker_leases import reserve
from loregarden.services.docker_ledger import OCCUPYING, load_pool
from loregarden.services.gate_capacity import GateCapacityUnavailable, acquire_gate_capacity
from loregarden.services.gate_runner import run_transition_gates
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from sqlmodel import Session, col, select
from tests.factories import make_ticket, make_workspace
from tests.test_capacity_host_pool import _reserve, _set_ceiling
from tests.test_orchestration_capacity import _acquire, _orch_run

PARENT_CPUS = 0.5


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host", autouse=True)
def host_fixture(session):
    """7.0 cpu / 48332 MB / 6 slots — this machine, probed 2026-11-02.

    The docker pool is pinned too (5.0 / 9942 / 4, probed 2026-10-27): one case
    below reserves in it to prove `parent_lease_for` ignores it, and an
    unmeasured ceiling would refuse that claim `docker_unavailable` rather than
    exercising the filter.
    """
    _set_ceiling(session, CapacityPool.HOST, cpus=7.0, memory_mb=48332, leases=6)
    _set_ceiling(session, CapacityPool.DOCKER, cpus=5.0, memory_mb=9942, leases=4)


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    workspace = make_workspace(session, slug="parent-hazards")
    workspace.repo_path = str(tmp_path)
    session.add(workspace)
    session.commit()
    return workspace


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    return make_ticket(
        session,
        workspace_id=workspace.id,
        external_id="lg-machine-resource-1923",
        state=TicketState.IN_PROGRESS,
    )


def _profile(commands: list[str], footprint=DockerFootprint.SERVICE) -> OrchestrationProfile:
    return OrchestrationProfile(
        slug="parent-hazards",
        gates=GatesConfig(enabled=True, commands=commands, capacity_footprint=footprint),
    )


def _host_leases(session, *, run_id: str | None = None) -> list[DockerLease]:
    session.expire_all()
    statement = select(DockerLease).where(DockerLease.pool == CapacityPool.HOST)
    if run_id is not None:
        statement = statement.where(DockerLease.orchestration_run_id == run_id)
    return list(session.exec(statement.order_by(col(DockerLease.requested_at))).all())


def _standing(session, orch_run) -> list[DockerLease]:
    """Every live ORCHESTRATION lease belonging to this run, nested or not.

    Deliberately NOT filtered on `parent_lease_id IS NULL`: a nested run's
    parent is a child of the outer run's, and a count that excludes it would
    report one standing claim while two are held.
    """
    return [
        lease
        for lease in _host_leases(session, run_id=orch_run.id)
        if lease.holder_kind is DockerHolderKind.ORCHESTRATION and lease.status in OCCUPYING
    ]


# ---- R1 + R8c: "exactly one" has to survive nesting --------------------------


def test_a_nested_runs_parent_is_found_again_rather_than_booked_twice(
    session, workspace, ticket
) -> None:
    """R1. A nested run's parent is a child, and the reuse lookup skips children.

    `held_for_run` filters `parent_lease_id IS NULL`, so an implementation that
    leans on `reserve`'s dedupe for idempotency is idempotent for a top-level
    run and silently not idempotent for a nested one. Every transition of the
    inner run would then book another 0.5 cpu of excess against the pools,
    ahead of the queue, for the life of the subtree — the bounded cost R7
    asserts, unbounded, by the one path `reserve` cannot see.

    `execute()` acquires lazily on the first transition gate, so for a nested
    ticket with three transitions this is three calls, not one.
    """
    outer_run = _orch_run(session, ticket, workspace)
    child_ticket = make_ticket(
        session, workspace_id=workspace.id, external_id="lg-machine-resource-1923-child"
    )
    inner_run = _orch_run(session, child_ticket, workspace)
    outer = _acquire(session, outer_run, ticket, workspace)
    assert outer.granted

    first = _acquire(
        session,
        inner_run,
        child_ticket,
        workspace,
        outer_parent_lease_id=outer.lease_id,
    )
    second = _acquire(
        session,
        inner_run,
        child_ticket,
        workspace,
        outer_parent_lease_id=outer.lease_id,
    )
    third = orchestration_capacity.parent_lease_for(session, inner_run)

    assert first.granted and second.granted
    assert second.lease_id == first.lease_id, (
        "the second transition of a nested run booked a second standing claim"
    )
    assert second.reused is True
    assert third is not None and third.id == first.lease_id, (
        "parent_lease_for must find a nested parent, which carries a parent_lease_id"
    )
    assert len(_standing(session, inner_run)) == 1
    # 0.5 outer + 0.5 inner, booked once: the inner claim's excess is paid once
    # because the inner parent exists once.
    assert load_pool(session, CapacityPool.HOST).held_cpus == 2 * PARENT_CPUS


def test_two_threads_acquiring_one_runs_parent_book_one_lease(
    isolated_db, session, workspace, ticket
) -> None:
    """R1. The dedupe is a read and a later insert, with nothing behind it.

    Two claimants for one run are reachable without any concurrency inside the
    orchestrator: a resumed run and the reaper's sweep, a server and
    `loregarden-cli`, an `execute()` recursing into a child whose own driver is
    already running. Both read "nothing held" and both insert.

    The assertion is the invariant, not the interleaving: however the two
    threads are scheduled, the run ends with one standing claim and the pool is
    charged for one. A second row here is not merely untidy — it is 0.5 cpu
    held by a run that will release one lease and leak the other, with a
    `holder_pid` that stays live and a reaper that will therefore never take it.
    """
    orch_run = _orch_run(session, ticket, workspace)
    run_id, ticket_id, workspace_id = orch_run.id, ticket.id, workspace.id
    ready = threading.Barrier(2)
    results: list[object] = []
    failures: list[BaseException] = []

    def claim() -> None:
        try:
            with Session(isolated_db) as scoped:
                run = scoped.get(OrchestrationRun, run_id)
                own_ticket = scoped.get(Ticket, ticket_id)
                own_workspace = scoped.get(Workspace, workspace_id)
                ready.wait(timeout=10)
                results.append(_acquire(scoped, run, own_ticket, own_workspace))
        except BaseException as error:  # noqa: BLE001 - re-raised below as a failure
            failures.append(error)
            ready.abort()

    threads = [threading.Thread(target=claim, name=f"claim-{index}") for index in range(2)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
        assert not thread.is_alive(), "a parent claim blocked forever; a holder must never wait"

    assert not failures, f"acquiring a parent raised rather than returning a refusal: {failures}"
    assert len(results) == 2
    standing = _standing(session, orch_run)
    assert len(standing) == 1, (
        f"two claimants booked {len(standing)} standing claims for one run; "
        "the loser must be handed the winner's lease, not a second grant"
    )
    assert {getattr(result, "lease_id", "") for result in results} == {standing[0].id}
    assert load_pool(session, CapacityPool.HOST).held_cpus == PARENT_CPUS


def test_parent_lease_for_answers_for_this_run_in_this_pool_only(
    session, workspace, ticket
) -> None:
    """R1. The lookup is keyed on the run, and a mutation matrix over the rows
    it must ignore: another run's parent, a released one, an agent run's lease
    stamped with the same orchestration id, and a DOCKER-pool claim.

    The agent-run row is the one that matters most: `run_capacity` stamps every
    agent-run host lease with the orchestration's id as well as its own, so a
    lookup that keys on `orchestration_run_id` alone finds a 0.25-cpu lease
    released by `RUN_COMPLETED` and hands it out as the run's parent — and the
    gate children then draw on a grant that disappears under them mid-gate.
    """
    orch_run = _orch_run(session, ticket, workspace)
    other_run = _orch_run(session, ticket, workspace)
    agent_run = AgentRun(
        run_code=f"run_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        agent_id="backend_implementer",
        stage_key="implement",
        status=RunStatus.RUNNING,
    )
    session.add(agent_run)
    session.commit()
    session.refresh(agent_run)

    agent = reserve(
        session,
        holder_label="agent run backend_implementer · stage implement",
        footprint=DockerFootprint.CUSTOM,
        cpus=0.25,
        memory_mb=768,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.AGENT_RUN,
        agent_run_id=agent_run.id,
        orchestration_run_id=orch_run.id,
        takes_slot=False,
    )
    docker_side = reserve(
        session,
        holder_label="compose stack · lg-machine-resource-1923",
        footprint=DockerFootprint.LIGHT,
        pool=CapacityPool.DOCKER,
        holder_kind=DockerHolderKind.ORCHESTRATION,
        orchestration_run_id=orch_run.id,
    )
    assert agent.granted and docker_side.granted

    assert orchestration_capacity.parent_lease_for(session, orch_run) is None, (
        "an agent run's lease, or a docker-pool claim, is not this run's host parent"
    )

    other = _acquire(session, other_run, ticket, workspace)
    assert other.granted
    assert orchestration_capacity.parent_lease_for(session, orch_run) is None, (
        "another run's parent on the same ticket is not this run's"
    )

    mine = _acquire(session, orch_run, ticket, workspace)
    found = orchestration_capacity.parent_lease_for(session, orch_run)
    assert found is not None and found.id == mine.lease_id
    assert found.id != agent.lease_id
    assert found.id != other.lease_id


# ---- R5: giving the parent back -----------------------------------------------


def test_a_park_gives_back_a_parent_that_still_has_a_live_gate_child(
    session, workspace, ticket
) -> None:
    """R5. A park can land while a transition is mid-flight.

    `release_for_park` must take the whole subtree with it. A parent released
    while a child is still OCCUPYING leaves the child's excess booked and its
    cover charged against a parent nobody will release again — capacity a
    person's inbox is now holding, with no dead pid for the reaper to find,
    which is the exact hazard the operator's decision was written to avoid.
    """
    orch_run = _orch_run(session, ticket, workspace, auto_approve=True)
    before = load_pool(session, CapacityPool.HOST).held_cpus
    parent = _acquire(session, orch_run, ticket, workspace)
    child = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="gates plan_to_plan-synthesis · mid-flight",
        footprint=DockerFootprint.SERVICE,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.AD_HOC,
    )
    assert child.granted

    released = orchestration_capacity.release_for_park(session, orch_run, ticket, None)

    assert released is True
    session.expire_all()
    parent_row = session.get(DockerLease, parent.lease_id)
    child_row = session.get(DockerLease, child.lease_id)
    assert parent_row.end_reason is DockerLeaseEndReason.PARK_RELEASED
    assert child_row.status not in OCCUPYING, "a child outlived the parent it draws on"
    assert child_row.end_reason is DockerLeaseEndReason.PARENT_RELEASED
    assert load_pool(session, CapacityPool.HOST).held_cpus == before, (
        "the park left capacity booked that nothing will release"
    )
    assert orchestration_capacity.parent_lease_for(session, orch_run) is None


def test_giving_the_parent_back_twice_credits_the_pool_once(session, workspace, ticket) -> None:
    """R5. Order-independence and idempotence, in the one direction that hurts.

    `execute()`'s `finally` is the backstop for paths no predicate enumerates,
    so `release_parent` runs after a park has already released the same lease —
    and after the reaper may have. `release_lease` is idempotent on purpose; the
    composite above it must not undo that. A pool credited twice admits past its
    own ceiling, and the symptom appears in somebody else's run.
    """
    orch_run = _orch_run(session, ticket, workspace, auto_approve=True)
    baseline = load_pool(session, CapacityPool.HOST).held_cpus
    parent = _acquire(session, orch_run, ticket, workspace)
    assert load_pool(session, CapacityPool.HOST).held_cpus == baseline + PARENT_CPUS

    assert orchestration_capacity.release_for_park(session, orch_run, ticket, None) is True
    first_release = load_pool(session, CapacityPool.HOST).held_cpus
    assert first_release == baseline

    assert orchestration_capacity.release_for_park(session, orch_run, ticket, None) is False, (
        "a park that released nothing must say so; True would be reported as a release"
    )
    assert orchestration_capacity.release_parent(session, orch_run) is False
    assert load_pool(session, CapacityPool.HOST).held_cpus == baseline, (
        "the pool was credited more than once for one lease"
    )
    row = session.get(DockerLease, parent.lease_id)
    assert row.end_reason is DockerLeaseEndReason.PARK_RELEASED, (
        "the second call overwrote why the lease ended; R9's re-acquire figure reads this"
    )


# ---- R2/R3: a gate whose parent is gone --------------------------------------


@pytest.mark.parametrize(
    ("state", "describe"),
    [("released", "a parent given back at a park"), ("unknown", "a lease id that never existed")],
)
def test_a_gate_whose_parent_is_gone_is_refused_not_promoted_to_a_top_level_claim(
    session, workspace, ticket, state, describe, monkeypatch
) -> None:
    """R2/R3, and the no-silent-failures rule.

    The tempting repair for "the parent went away" is to fall back to the
    top-level claim gates made before this ticket. That would be a wait of
    minutes to hours reintroduced on a path nothing reports, and the ticket
    would read as fixed. Refuse instead, and say the parent is gone: a run whose
    standing claim has been taken is not a run that should quietly rejoin the
    FIFO line mid-transition.

    `wait_for_grant` is replaced with a raise, so a fallback that queues is a
    failure here rather than a slow pass.
    """

    def never_waits(*args, **kwargs):
        raise AssertionError("a gate child queued; a holder that waits can deadlock")

    monkeypatch.setattr("loregarden.services.gate_capacity.wait_for_grant", never_waits)
    orch_run = _orch_run(session, ticket, workspace)
    if state == "released":
        parent = _acquire(session, orch_run, ticket, workspace)
        parent_lease_id = parent.lease_id
        assert orchestration_capacity.release_parent(session, orch_run) is True
    else:
        parent_lease_id = f"lease-{uuid4().hex}"
    before = load_pool(session, CapacityPool.HOST).held_cpus

    with pytest.raises(GateCapacityUnavailable) as refusal:
        acquire_gate_capacity(
            session,
            DockerFootprint.SERVICE,
            ticket=ticket,
            workspace=workspace,
            transition="plan_to_plan-synthesis",
            ttl_seconds=600,
            parent_lease_id=parent_lease_id,
        )

    message = str(refusal.value)
    assert "plan_to_plan-synthesis" in message, f"the refusal must name the transition: {message}"
    assert parent_lease_id in message, f"{describe}: the refusal must name the lease: {message}"
    session.expire_all()
    gate_rows = [
        lease
        for lease in _host_leases(session)
        if lease.holder_label.startswith("gates ") and lease.status in OCCUPYING
    ]
    assert gate_rows == [], "a refused gate left a lease holding capacity"
    assert load_pool(session, CapacityPool.HOST).held_cpus == before


def test_a_parent_footprint_of_none_never_passes_a_gate_with_nothing_held(
    session, workspace, ticket, tmp_path
) -> None:
    """Assumption check on `gates.parent_capacity_footprint`.

    The ticket makes the parent's size config so retuning is not a code change,
    and names no domain for the setting. `NONE` and a zero-cpu CUSTOM are
    therefore reachable from a profile, and the one answer that is wrong under
    either reading of the ticket is a gate that runs its commands holding
    nothing while the ledger is on: that is the silent skip R3 exists to
    forbid, and it would make every gate on the machine unmetered.

    Both defensible readings pass here — fall back to the top-level claim gates
    made before this ticket, or report UNAVAILABLE — so this pins the floor
    without settling the fork. The implementer picks one; see the checkpoint.
    """
    orch_run = _orch_run(session, ticket, workspace)
    profile = OrchestrationProfile(
        slug="parent-none",
        gates=GatesConfig(
            enabled=True,
            commands=["true"],
            capacity_footprint=DockerFootprint.SERVICE,
            parent_capacity_footprint=DockerFootprint.NONE,
        ),
    )

    result = run_transition_gates(
        session,
        profile,
        workspace,
        ticket,
        from_stage="triage",
        to_stage="plan",
        orch_run=orch_run,
    )

    if result.outcome is GateOutcome.UNAVAILABLE:
        assert not result.ok
        assert _standing(session, orch_run) == []
        return
    assert result.outcome is GateOutcome.PASSED, result.message
    gate_rows = [
        lease for lease in _host_leases(session) if lease.holder_label.startswith("gates ")
    ]
    assert gate_rows, "the gate commands ran holding no lease at all, on a metered machine"
    assert all(row.cpus > 0 for row in gate_rows), "a zero-cpu gate lease meters nothing"


# ---- R8b: cover returned, transition after transition -------------------------


def test_six_transitions_in_one_run_each_get_their_cover_back(session, workspace, ticket) -> None:
    """R1/R8b. The run has more than two transitions, and the cover is finite.

    A LIGHT parent lends 0.5 cpu to each SERVICE child. The lending is a
    conditional UPDATE on `parent.child_covered_cpus`; a release that does not
    reverse it leaves the parent fully lent after one transition, and every
    later gate is refused `exceeds_parent_grant`. Two transitions cannot see
    that — the first lends and the second is the first that could be refused —
    so this runs the six a real workflow has and checks the parent's books
    between each one.

    Stress, and an order-dependency check: the sixth transition must cost the
    pool exactly what the first did.
    """
    orch_run = _orch_run(session, ticket, workspace)
    profile = _profile(["true"])
    stages = ["triage", "plan", "plan-synthesis", "spec", "test-design", "test-break", "implement"]
    costs: list[float] = []

    for from_stage, to_stage in zip(stages, stages[1:], strict=False):
        result = run_transition_gates(
            session,
            profile,
            workspace,
            ticket,
            from_stage=from_stage,
            to_stage=to_stage,
            orch_run=orch_run,
        )
        assert result.outcome is GateOutcome.PASSED, (
            f"{from_stage}->{to_stage} failed: {result.message}"
        )
        session.expire_all()
        (parent,) = [
            lease
            for lease in _host_leases(session, run_id=orch_run.id)
            if lease.holder_kind is DockerHolderKind.ORCHESTRATION
        ]
        assert parent.status is DockerLeaseStatus.HELD
        assert parent.child_covered_cpus == pytest.approx(0.0), (
            f"after {from_stage}->{to_stage} the parent is still lending "
            f"{parent.child_covered_cpus} cpu to a child that has ended"
        )
        assert parent.child_covered_memory_mb == 0
        costs.append(load_pool(session, CapacityPool.HOST).held_cpus)

    assert costs == [pytest.approx(PARENT_CPUS)] * len(costs), (
        "between transitions the run holds its parent and nothing else"
    )
    children = [
        lease for lease in _host_leases(session, run_id=orch_run.id) if lease.parent_lease_id
    ]
    assert len(children) == len(stages) - 1
    assert {child.status for child in children} == {DockerLeaseStatus.RELEASED}
    assert all(child.granted_at == child.requested_at for child in children), (
        "a child spent time in the line"
    )


def test_a_gate_child_refused_for_its_own_size_leaves_the_parents_books_straight(
    session, workspace, ticket
) -> None:
    """R2. A refusal must not half-lend.

    `reserve_child` covers the child from the parent and books the excess in
    one transaction, retrying a lost race. The failure mode is a refusal that
    has already incremented `child_covered_cpus`: the parent then lends
    capacity to a lease that does not exist, and the next gate — which fits —
    is refused for a remainder that was never spent. Nothing recovers it,
    because the phantom child has no row for the sweep in R8b to find.
    """
    orch_run = _orch_run(session, ticket, workspace)
    parent = _acquire(session, orch_run, ticket, workspace)
    _reserve(session, CapacityPool.HOST, label="pre-push server-tests", cpus=6.5)

    refused = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="gates plan_to_plan-synthesis · too big",
        footprint=DockerFootprint.SERVICE,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.AD_HOC,
    )

    assert refused.state is DockerGrantState.REJECTED
    assert refused.state is not DockerGrantState.QUEUED
    session.expire_all()
    parent_row = session.get(DockerLease, parent.lease_id)
    assert parent_row.child_covered_cpus == pytest.approx(0.0), (
        "a refused child left the parent lending to nothing"
    )
    assert parent_row.child_covered_memory_mb == 0

    # And the next child, which fits inside the grant, is still granted.
    fits = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="gates spec_to_test-design · fits",
        footprint=DockerFootprint.LIGHT,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.AD_HOC,
    )
    assert fits.granted, fits.message
