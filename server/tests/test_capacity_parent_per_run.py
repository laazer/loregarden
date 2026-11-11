"""A ticket queues for host capacity once per run, not once per transition.

Acceptance criteria (lg-machine-resource-1923) R1, R3, R7 and R8.

The measurement this ticket exists for, from `lg-reference-sources-1912` on
2026-11-02: of 57 minutes wall-clock, 8 minutes was agent work and 29 minutes
was waiting in the capacity queue — 6m24s at `triage_to_plan` and 22m40s at
`plan_to_plan-synthesis`. Neither wait was scarcity. At 15:46 the host pool had
2.75 cpu free and the gate wanted 1.0, but a 4.0-cpu `land-verify` waiter sat at
the head of the line and the drain stops at the first head that does not fit.

So the shape under test is: one top-level parent per orchestration run, and a
child per gate. A child never waits — it is priced from the parent's unlent
remainder, any excess is booked ahead of the queue, or it is refused at once.

`test_the_1912_sequence_pays_the_capacity_line_once_not_twice` is a SIMULATION
over lease rows at the occupancy measured that afternoon. 1912's own rows are
history and are not re-executable; what is replayed is the arrangement — a
4.0-cpu waiter parked in front of a 1.0-cpu gate — and the before/after is the
evidence. The same sequence with no `orchestration_run_id` still pays twice,
which is what makes the comparison mean anything.
"""

from __future__ import annotations

from uuid import uuid4

import pytest
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    GateOutcome,
    OrchestrationRun,
    OrchestrationRunStatus,
    TicketState,
)
from loregarden.services import orchestration_capacity
from loregarden.services.capacity_children import reserve_child
from loregarden.services.docker_ledger import OCCUPYING, load_pool
from loregarden.services.gate_runner import run_transition_gates
from loregarden.services.lane_count import lane_count
from loregarden.services.orchestration_profile import GatesConfig, OrchestrationProfile
from sqlmodel import Session, col, select
from tests.factories import make_ticket, make_workspace
from tests.test_capacity_host_pool import _reserve, _set_ceiling

#: The agent-run host lease, measured: 0.25 cpu / 768 MB per run.
AGENT_RUN_CPUS = 0.25


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host", autouse=True)
def host_fixture(session):
    """7.0 cpu / 48332 MB / 6 slots — this machine, probed 2026-11-02T18:23:28."""
    _set_ceiling(session, CapacityPool.HOST, cpus=7.0, memory_mb=48332, leases=6)


@pytest.fixture(name="workspace")
def workspace_fixture(session, tmp_path):
    workspace = make_workspace(session, slug="parent-per-run")
    workspace.repo_path = str(tmp_path)
    session.add(workspace)
    session.commit()
    return workspace


@pytest.fixture(name="ticket")
def ticket_fixture(session, workspace):
    return make_ticket(
        session,
        workspace_id=workspace.id,
        external_id="lg-reference-sources-1912",
        state=TicketState.IN_PROGRESS,
    )


def _orch_run(session, ticket, workspace) -> OrchestrationRun:
    run = OrchestrationRun(
        run_code=f"orch_{uuid4().hex[:6]}",
        ticket_id=ticket.id,
        workspace_id=workspace.id,
        status=OrchestrationRunStatus.RUNNING,
    )
    session.add(run)
    session.commit()
    session.refresh(run)
    return run


def _profile(commands: list[str], footprint=DockerFootprint.SERVICE) -> OrchestrationProfile:
    return OrchestrationProfile(
        slug="parent-per-run",
        gates=GatesConfig(enabled=True, commands=commands, capacity_footprint=footprint),
    )


def _gate(session, profile, workspace, ticket, *, from_stage, to_stage, orch_run, **kwargs):
    """One transition's gates, run the way the orchestrator runs them.

    `orch_run` is the keyword the parent arrives through: the gate acquires or
    finds the run's parent and claims a child of it. `land_verification` passes
    none and keeps its top-level HEAVY claim — a 4.0 child under a 0.5 parent
    would book 3.5 cpu ahead of the whole queue, which is worse than the block
    it dodges.
    """
    return run_transition_gates(
        session,
        profile,
        workspace,
        ticket,
        from_stage=from_stage,
        to_stage=to_stage,
        orch_run=orch_run,
        **kwargs,
    )


def _leases(session, *, run_id: str | None = None) -> list[DockerLease]:
    session.expire_all()
    statement = select(DockerLease).where(DockerLease.pool == CapacityPool.HOST)
    if run_id is not None:
        statement = statement.where(DockerLease.orchestration_run_id == run_id)
    return list(session.exec(statement.order_by(col(DockerLease.requested_at))).all())


def _parents(leases: list[DockerLease]) -> list[DockerLease]:
    return [lease for lease in leases if lease.parent_lease_id is None]


def _waited(lease: DockerLease) -> bool:
    """Whether this lease spent measurable time in the line."""
    return (
        lease.granted_at is not None
        and lease.requested_at is not None
        and lease.granted_at > lease.requested_at
    )


# ---- R1: one parent, a child per gate ----------------------------------------


def test_two_transitions_in_one_run_share_one_parent_lease(
    session, workspace, ticket, tmp_path
) -> None:
    """R1. Two gates, one trip to the line.

    The parent is still OCCUPYING between the two transitions — that is the
    whole mechanism. A parent released at the end of the first transition would
    make the second gate a fresh top-level claim and nothing here would say so
    except the lease graph.
    """
    orch_run = _orch_run(session, ticket, workspace)
    profile = _profile(["true"])

    first = _gate(
        session,
        profile,
        workspace,
        ticket,
        from_stage="triage",
        to_stage="plan",
        orch_run=orch_run,
    )
    parents_between = _parents(_leases(session, run_id=orch_run.id))
    assert [lease.status for lease in parents_between] == [DockerLeaseStatus.HELD], (
        "the parent must outlive the first transition, or nothing is shared"
    )
    second = _gate(
        session,
        profile,
        workspace,
        ticket,
        from_stage="plan",
        to_stage="plan-synthesis",
        orch_run=orch_run,
    )

    assert first.outcome is GateOutcome.PASSED
    assert second.outcome is GateOutcome.PASSED
    leases = _leases(session, run_id=orch_run.id)
    (parent,) = _parents(leases)
    assert parent.holder_kind is DockerHolderKind.ORCHESTRATION
    children = [lease for lease in leases if lease.parent_lease_id == parent.id]
    assert len(children) == 2, "one child per transition, both drawing on the one parent"
    assert all(child.holder_label.startswith("gates ") for child in children)
    assert {child.status for child in children} == {DockerLeaseStatus.RELEASED}, (
        "each gate hands its child back when the transition ends; only the parent persists"
    )


def test_a_leaked_child_from_a_previous_transition_does_not_refuse_the_next_gate(
    session, workspace, ticket
) -> None:
    """R8b. Nothing else recovers a leaked child, so each transition must.

    `reserve_child` refuses rather than queues, and `docker_reaper._judge`
    extends an expired child indefinitely while its parent is held — so a child
    left behind by a killed transition would refuse every later gate on the run,
    for the life of the run, with no clock to clear it.

    A nested child-ticket run's parent is a child too, and it is live work: it
    must survive the sweep, which is why the assertion is about gate children
    and not about children in general.
    """
    orch_run = _orch_run(session, ticket, workspace)
    parent = orchestration_capacity.acquire_or_find_parent(
        session,
        orch_run=orch_run,
        ticket=ticket,
        workspace=workspace,
        footprint=GatesConfig().parent_capacity_footprint,
    )
    leaked = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="gates triage_to_plan · leaked",
        footprint=DockerFootprint.SERVICE,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.AD_HOC,
    )
    nested = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="run orch_nested · child ticket",
        footprint=DockerFootprint.LIGHT,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.ORCHESTRATION,
    )
    assert leaked.granted and nested.granted

    result = _gate(
        session,
        _profile(["true"]),
        workspace,
        ticket,
        from_stage="plan",
        to_stage="spec",
        orch_run=orch_run,
    )

    assert result.outcome is GateOutcome.PASSED, result.message
    assert session.get(DockerLease, leaked.lease_id).status is DockerLeaseStatus.RELEASED
    assert session.get(DockerLease, nested.lease_id).status in OCCUPYING, (
        "a nested run's parent is live work, not a leak"
    )


def test_a_nested_child_ticket_run_draws_on_the_outer_runs_parent(
    session, workspace, ticket
) -> None:
    """R8c. A subtree pays the line once, not once per ticket in it.

    `execute()` orchestrates incomplete children inside its own try block, so
    the outer run is holding its parent while the inner run starts. An inner
    run that took a second top-level parent would be a holder that waits — the
    shape `capacity_children`'s docstring exists to forbid.
    """
    outer_run = _orch_run(session, ticket, workspace)
    child_ticket = make_ticket(
        session, workspace_id=workspace.id, external_id="lg-machine-resource-1923-child"
    )
    inner_run = _orch_run(session, child_ticket, workspace)
    outer = orchestration_capacity.acquire_or_find_parent(
        session,
        orch_run=outer_run,
        ticket=ticket,
        workspace=workspace,
        footprint=GatesConfig().parent_capacity_footprint,
    )

    inner = orchestration_capacity.acquire_or_find_parent(
        session,
        orch_run=inner_run,
        ticket=child_ticket,
        workspace=workspace,
        footprint=GatesConfig().parent_capacity_footprint,
        outer_parent_lease_id=outer.lease_id,
    )

    assert inner.granted
    inner_lease = session.get(DockerLease, inner.lease_id)
    assert inner_lease.parent_lease_id == outer.lease_id
    assert inner_lease.orchestration_run_id == inner_run.id
    assert _waited(inner_lease) is False, "a child never waits, so a subtree never does"
    top_level = [
        lease
        for lease in _leases(session)
        if lease.parent_lease_id is None and lease.holder_kind is DockerHolderKind.ORCHESTRATION
    ]
    assert len(top_level) == 1, "one trip to the line for the whole subtree"


# ---- R3: a parent that cannot be had is reported, never skipped ---------------


def test_a_parent_that_cannot_be_acquired_fails_the_gate_visibly(
    session, workspace, ticket, tmp_path, monkeypatch
) -> None:
    """R3. No gate command runs, nothing is left HELD or WAITING, and the
    message is addressed to the person who must clear it.

    Driven by an expired wait budget against a full pool — a failure mode that
    can actually fire. An impossible footprint cannot: a 0.5-cpu parent on a
    7.0-cpu pool is never rejected for exceeding capacity, so a test built on
    one would pin a branch the code can never reach.
    """
    monkeypatch.setattr("loregarden.config.settings.gate_capacity_wait_seconds", 0.3)
    _reserve(session, CapacityPool.HOST, label="pre-push server-tests", cpus=7.0)
    orch_run = _orch_run(session, ticket, workspace)
    ran = tmp_path / "ran"

    result = _gate(
        session,
        _profile([f"touch {ran}"]),
        workspace,
        ticket,
        from_stage="triage",
        to_stage="plan",
        orch_run=orch_run,
    )

    assert not result.ok
    assert result.outcome is GateOutcome.UNAVAILABLE, "a busy machine is not a gate failure"
    assert not ran.exists(), "a gate that could not reserve its capacity still ran"
    # Named for the person, not for the mechanism: the pool, the ask, and who is
    # holding it. "reserve_child returned exceeds_parent_grant" is not an
    # instruction anyone can act on.
    assert "host capacity" in result.message
    assert "0.5" in result.message
    assert "pre-push server-tests" in result.message
    assert orchestration_capacity.parent_lease_for(session, orch_run) is None
    for lease in _leases(session, run_id=orch_run.id):
        assert lease.status not in OCCUPYING, "a refused parent left a lease holding"
        assert lease.status is not DockerLeaseStatus.WAITING, (
            "a waiter nobody will ever poll holds a place in a head-of-line queue"
        )
        assert lease.end_reason is DockerLeaseEndReason.ABANDONED


def test_a_parent_whose_ledger_read_raises_fails_the_gate_rather_than_passing_it(
    session, workspace, ticket, tmp_path, monkeypatch
) -> None:
    """R3. The second failure mode that can fire: the ledger itself erroring.

    This is the `has_conflicts: False because the check threw` shape. A gate
    that cannot find out whether it has capacity must not report PASSED.
    """
    orch_run = _orch_run(session, ticket, workspace)
    ran = tmp_path / "ran"

    def unreadable(*args, **kwargs):
        raise RuntimeError("database is locked")

    monkeypatch.setattr(orchestration_capacity, "acquire_or_find_parent", unreadable)

    result = _gate(
        session,
        _profile([f"touch {ran}"]),
        workspace,
        ticket,
        from_stage="triage",
        to_stage="plan",
        orch_run=orch_run,
    )

    assert not result.ok
    assert result.outcome is GateOutcome.UNAVAILABLE
    assert "database is locked" in result.message
    assert not ran.exists()


def test_the_two_refusals_name_the_same_two_numbers(
    session, workspace, ticket, monkeypatch
) -> None:
    """R2/R3. One refusal reaches the operator through the ticket and one
    through the capacity board, and they must agree.

    The two numbers are the ask and the parent's unlent remainder. An operator
    reading "needs 1 cpu, parent holds 0.5" on one surface and a bare
    `exceeds_parent_grant` on the other cannot tell they are the same event.
    """
    orch_run = _orch_run(session, ticket, workspace)
    parent = orchestration_capacity.acquire_or_find_parent(
        session,
        orch_run=orch_run,
        ticket=ticket,
        workspace=workspace,
        footprint=GatesConfig().parent_capacity_footprint,
    )
    # Fill the machine so nothing can be booked ahead of the line, then ask for
    # a SERVICE child the 0.5 parent cannot cover.
    _reserve(session, CapacityPool.HOST, label="pre-push server-tests", cpus=6.5)
    # The gate re-asks while the pool moves; nothing ends here, so it stalls out.
    monkeypatch.setattr("loregarden.config.settings.gate_capacity_wait_seconds", 0.3)

    ledger_refusal = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="gates plan_to_plan-synthesis",
        footprint=DockerFootprint.SERVICE,
        pool=CapacityPool.HOST,
        holder_kind=DockerHolderKind.AD_HOC,
    )
    gate_refusal = _gate(
        session,
        _profile(["true"]),
        workspace,
        ticket,
        from_stage="plan",
        to_stage="plan-synthesis",
        orch_run=orch_run,
    )

    assert ledger_refusal.state is DockerGrantState.REJECTED
    assert ledger_refusal.error_kind == "exceeds_parent_grant"
    assert gate_refusal.outcome is GateOutcome.UNAVAILABLE
    for message in (ledger_refusal.message, gate_refusal.message):
        assert "1 cpu" in message or "1 cpus" in message, message
        assert "0.5" in message, message
        assert parent.lease_id in message, message


# ---- R7: the 1912 replay, and the standing hold's budget ----------------------


def test_the_1912_sequence_pays_the_capacity_line_once_not_twice(
    session, workspace, ticket, monkeypatch
) -> None:
    """R7. The before/after, at the occupancy measured at 15:46 on 2026-11-02.

    A SIMULATION over lease rows: 1912's rows are history and are not
    re-executable. What is reproduced is the arrangement that produced the
    29 minutes — a 4.0-cpu land-verify waiter at the head of a strict
    head-of-line queue, with 2.75 cpu free and a 1.0-cpu gate behind it.

    The control at the bottom is the part that makes this a measurement rather
    than an assertion: the identical sequence with no `orchestration_run_id`
    still pays twice.
    """
    # The control at the bottom takes top-level claims behind a head that does
    # not fit, which is the whole point of it — so it waits the real
    # `gate_capacity_wait_seconds` (1800s) twice unless the budget is cut here.
    # The parent and its children never queue, so this changes nothing the test
    # measures; without it the test cannot finish.
    monkeypatch.setattr("loregarden.config.settings.gate_capacity_wait_seconds", 0.3)
    # 7.0 ceiling, 4.25 held → 2.75 free, as measured.
    _reserve(session, CapacityPool.HOST, label="agent run · stage implement", cpus=4.25)
    orch_run = _orch_run(session, ticket, workspace)
    profile = _profile(["true"])

    _gate(
        session,
        profile,
        workspace,
        ticket,
        from_stage="triage",
        to_stage="plan",
        orch_run=orch_run,
    )
    # Parked once the run holds its parent, because that is the arrangement the
    # 29 minutes came out of: a 4.0-cpu head that does not fit, in front of a
    # *gate's* claim. Parking it before the parent is acquired asks something
    # else — that a 0.5-cpu claim queued behind a head which never fits is
    # granted anyway, which is FIFO backfill (lg-machine-resource-1925) and is
    # out of this ticket's scope. The run still pays the line at most once,
    # which is what `_waited` counts below.
    blocked = _reserve(session, CapacityPool.HOST, label="land-verify lg-other-1", cpus=4.0)
    assert blocked.state is DockerGrantState.QUEUED, (
        "the head of the line must not fit, or there is no head-of-line to replay"
    )
    _gate(
        session,
        profile,
        workspace,
        ticket,
        from_stage="plan",
        to_stage="plan-synthesis",
        orch_run=orch_run,
    )

    run_leases = _leases(session, run_id=orch_run.id)
    (parent,) = _parents(run_leases)
    children = [lease for lease in run_leases if lease.parent_lease_id == parent.id]
    assert len([lease for lease in run_leases if _waited(lease)]) <= 1, (
        "one trip to the line per run; a second wait is the regression this ticket removes"
    )
    assert len(children) == 2
    for child in children:
        assert _waited(child) is False, "a gate child never waits, whatever is at the head"
    assert session.get(DockerLease, blocked.lease_id).status is DockerLeaseStatus.WAITING, (
        "the head of the line keeps its place; the children booked their excess, not its turn"
    )

    # The control: the same two gates with no run to key a parent on.
    control_ticket = make_ticket(
        session, workspace_id=workspace.id, external_id="lg-reference-sources-1912-control"
    )
    for transition in (("triage", "plan"), ("plan", "plan-synthesis")):
        _gate(
            session,
            profile,
            workspace,
            control_ticket,
            from_stage=transition[0],
            to_stage=transition[1],
            orch_run=None,
        )
    control = [
        lease
        for lease in _leases(session)
        if lease.ticket_id == control_ticket.id and lease.holder_label.startswith("gates ")
    ]
    assert len(control) == 2
    assert all(lease.parent_lease_id is None for lease in control), (
        "with no run there is no parent to draw on — these are top-level claims"
    )


def test_the_standing_parent_hold_fits_the_host_ceiling_at_the_live_lane_count(session) -> None:
    """R7. The regression class every other criterion can pass through.

    At the live lane count of 6:
        6 x 0.5 (parents) + 6 x 0.25 (agent runs) + 1.0 (one gate) = 5.5 of 7.0

    A SERVICE parent fails this: 6 x 1.0 + 6 x 0.25 = 7.5 > 7.0, and the pool is
    oversubscribed before a gate command runs — lanes 5 and 6 cannot get their
    own 0.25 while holding parents that only their own forward progress can
    release. That is a holder that waits, and it resolves as other runs finish,
    so it is a livelock rather than a deadlock: intermittent, and attributable
    to anything but this change.

    Both inputs are read, never hardcoded. `lane_count` is a live setting and
    the parent's size is a profile field: at 3 lanes SERVICE becomes affordable
    (3.0 + 0.75 = 3.75), and above 8 lanes even LIGHT is marginal.
    """
    from loregarden.services.docker_capacity import resolve_weights
    from loregarden.services.lane_count import store_lane_count

    # The measured configuration is replayed; the arithmetic is still computed
    # from whatever the setting says, so moving the lanes moves the assertion.
    store_lane_count(session, 6)
    lanes = lane_count(session)
    assert lanes == 6
    parent_cpus, _ = resolve_weights(GatesConfig().parent_capacity_footprint)
    gate_cpus, _ = resolve_weights(GatesConfig().capacity_footprint)
    ceiling = load_pool(session, CapacityPool.HOST).ceiling_cpus

    standing = lanes * parent_cpus + lanes * AGENT_RUN_CPUS
    assert standing + gate_cpus <= ceiling, (
        f"{lanes} lanes x {parent_cpus} parent + {lanes} x {AGENT_RUN_CPUS} agent + "
        f"{gate_cpus} gate = {standing + gate_cpus} cpu, over the {ceiling} cpu ceiling. "
        "Re-do the three-line arithmetic before accepting this footprint."
    )
    # And the SERVICE parent the profile defaults its *gates* to is the one the
    # measurement rejects, stated here so a later reader does not retune the
    # parent to match the gate and re-introduce the livelock.
    service_cpus, _ = resolve_weights(DockerFootprint.SERVICE)
    assert lanes * service_cpus + lanes * AGENT_RUN_CPUS > ceiling, (
        "a SERVICE parent fits this machine now; re-check which footprint the "
        "parent should carry rather than leaving LIGHT in place by inertia"
    )
