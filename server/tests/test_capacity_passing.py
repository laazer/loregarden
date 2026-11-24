"""A small standing claim may start ahead of a blocked head, and never delays it.

An agent run wants 0.25 cpus. Behind a 4-cpu suite at the head of the line it
waited up to 111 minutes for capacity that was free the whole time.

The risk is the one strict order existed to prevent: a stream of small claims
that keeps the head from ever fitting. So beside the cases, the rule is checked
over random machines against what it promises — **whatever order the running work
finishes in, the head starts on the release it would have started on anyway.**
"""

from __future__ import annotations

import itertools
import random
import threading
from datetime import timedelta

import pytest
from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    DockerFootprint,
    DockerHolderKind,
    DockerLease,
    DockerLeaseEndReason,
    DockerLeaseStatus,
    DockerWaitBasis,
)
from loregarden.services import docker_leases
from loregarden.services.capacity_children import reserve_child
from loregarden.services.capacity_line import leases_ahead, retry_while_pool_moves
from loregarden.services.capacity_passing import Room, passing_leaves_head_alone
from loregarden.services.docker_leases import DockerReservation
from loregarden.services.docker_ledger import load_pool
from loregarden.services.docker_wait_estimate import estimate_waits
from sqlmodel import Session, select
from tests.test_capacity_host_pool import _reserve as _work
from tests.test_capacity_host_pool import _set_ceiling
from tests.test_docker_wait_estimate import NOW, _history

HEAVY = 4.0
AGENT = DockerHolderKind.AGENT_RUN
PARENT = DockerHolderKind.ORCHESTRATION


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        # The development machine's host pool.
        _set_ceiling(session, CapacityPool.HOST, cpus=7, memory_mb=48_000, leases=6)
        _set_ceiling(session, CapacityPool.DOCKER, cpus=6.5, memory_mb=9_929, leases=4)
        yield session


def _heavy(session: Session, label: str, *, cpus: float = HEAVY, memory_mb: int = 8192):
    return _work(session, CapacityPool.HOST, label=label, cpus=cpus, memory_mb=memory_mb)


def _standing(
    session: Session,
    label: str,
    *,
    cpus: float = 0.25,
    memory_mb: int = 768,
    kind: DockerHolderKind = AGENT,
    takes_slot: bool = False,
) -> DockerReservation:
    return docker_leases.reserve(
        session,
        holder_label=label,
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=memory_mb,
        holder_kind=kind,
        pool=CapacityPool.HOST,
        takes_slot=takes_slot,
    )


def _blocked_line(session: Session) -> tuple[DockerReservation, DockerReservation]:
    """A running 4-cpu suite, and another at the head that does not fit beside it."""
    running = _heavy(session, "running suite")
    head = _heavy(session, "head suite")
    assert running.granted and not head.granted
    return running, head


def _lease(session: Session, lease_id: str) -> DockerLease:
    session.expire_all()
    lease = session.get(DockerLease, lease_id)
    assert lease is not None
    return lease


def test_an_agent_run_starts_past_a_blocked_suite(session) -> None:
    _, head = _blocked_line(session)

    assert _standing(session, "agent run").granted
    assert _lease(session, head.lease_id).status is DockerLeaseStatus.WAITING


def test_so_does_an_orchestrations_standing_lease(session) -> None:
    _blocked_line(session)

    assert _standing(session, "run orch_1", cpus=0.5, memory_mb=512, kind=PARENT).granted


@pytest.mark.parametrize(
    "claim",
    [
        pytest.param({"kind": DockerHolderKind.AD_HOC}, id="ad-hoc work"),
        pytest.param({"takes_slot": True}, id="a claim that takes a slot"),
        pytest.param({"cpus": 1.0}, id="a claim as big as work"),
    ],
)
def test_nothing_else_passes(session, claim) -> None:
    _blocked_line(session)

    assert not _standing(session, "other", **claim).granted


def test_a_docker_claim_never_passes(session) -> None:
    _blocked_line(session)

    claim = docker_leases.reserve(
        session,
        holder_label="stage stack",
        footprint=DockerFootprint.CUSTOM,
        cpus=0.25,
        memory_mb=256,
        holder_kind=AGENT,
        pool=CapacityPool.DOCKER,
        takes_slot=False,
    )

    assert not claim.granted


def test_agents_pass_only_until_the_head_would_have_to_wait_for_them(session) -> None:
    running, head = _blocked_line(session)

    # 7 cpus less the head's 4 leaves 3 for everything that stands: 12 agents.
    # A thirteenth would fit the 3 free now as little as it would fit beside the head.
    passed = [_standing(session, f"agent {n}") for n in range(13)]
    assert [claim.granted for claim in passed] == [True] * 12 + [False]

    running.release()
    assert _lease(session, head.lease_id).status is DockerLeaseStatus.HELD


def test_nothing_passes_a_head_only_standing_claims_hold_back(session) -> None:
    """The stream that would starve it: a 2-cpu service that never ends, and agents
    already holding more than the head can spare."""
    _heavy(session, "service", cpus=2.0)
    agents = [_standing(session, f"agent {n}") for n in range(6)]
    assert all(agent.granted for agent in agents)
    head = _heavy(session, "head suite")
    assert not head.granted, "2 + 1.5 + 4 is over 7"

    assert not _standing(session, "late agent").granted
    for agent in agents[:2]:
        agent.release()
    assert _lease(session, head.lease_id).status is DockerLeaseStatus.HELD


def test_running_work_that_may_stay_is_allowed_for(session) -> None:
    """Two 2-cpu stacks run. Either may outlive the other, so only 1 cpu may pass,
    though 3 are free."""
    _heavy(session, "stack a", cpus=2.0, memory_mb=1024)
    second = _heavy(session, "stack b", cpus=2.0, memory_mb=1024)
    head = _heavy(session, "head suite")
    assert not head.granted

    passed = [_standing(session, f"agent {n}").granted for n in range(5)]
    assert passed == [True] * 4 + [False]

    second.release()
    assert _lease(session, head.lease_id).status is DockerLeaseStatus.HELD


def test_memory_is_protected_as_cpus_are(session) -> None:
    _set_ceiling(session, CapacityPool.HOST, cpus=7, memory_mb=9_500, leases=6)
    running = _heavy(session, "running suite")
    head = _heavy(session, "head suite")

    # 9500 less the head's 8192 is 1308 MB: one 768 MB agent, not two.
    assert _standing(session, "agent 0").granted
    assert not _standing(session, "agent 1").granted
    running.release()
    assert _lease(session, head.lease_id).status is DockerLeaseStatus.HELD


def test_a_gate_childs_excess_counts_as_work_that_finishes(session) -> None:
    """A gate child is booked ahead of the line. Read as standing it would close
    the line to agents for as long as any gate ran."""
    parent = _standing(session, "run orch_1", cpus=0.5, memory_mb=512, kind=PARENT)
    child = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="gates implement→verify",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.5,
        memory_mb=1024,
        pool=CapacityPool.HOST,
    )
    assert child.granted, "1 cpu of excess"
    _blocked_line(session)

    # 0.5 + 1 + 4 held: 1.5 free. Standing may reach 7 - 4 = 3 once the suite
    # ends, or 2 while the gate outlives it: so six more agents, all that fit.
    passed = [_standing(session, f"agent {n}").granted for n in range(6)]
    assert passed == [True] * 6


def test_a_gate_child_leaves_agents_their_share(session) -> None:
    """Two stacks and a gate's 1.5 cpus run. Counted as standing, the gate would
    leave agents nothing: 2 + 4 + 1.5 is over 7 before any of them starts."""
    parent = _standing(session, "run orch_1", cpus=0.5, memory_mb=512, kind=PARENT)
    _heavy(session, "stack a", cpus=2.0, memory_mb=1024)
    _heavy(session, "stack b", cpus=2.0, memory_mb=1024)
    child = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="gates implement→verify",
        footprint=DockerFootprint.CUSTOM,
        cpus=2.0,
        memory_mb=1024,
        pool=CapacityPool.HOST,
    )
    assert child.granted, "1.5 cpus of excess"
    assert not _heavy(session, "head suite").granted

    passed = [_standing(session, f"agent {n}").granted for n in range(3)]
    assert passed == [True, True, False]


def test_a_docker_stack_counts_as_work(session) -> None:
    stack = docker_leases.reserve(
        session,
        holder_label="compose stack",
        footprint=DockerFootprint.CUSTOM,
        cpus=4.0,
        memory_mb=4096,
        pool=CapacityPool.DOCKER,
    )
    assert stack.granted
    assert not _heavy(session, "head suite").granted

    assert _standing(session, "agent run").granted


def test_a_heavy_agent_runtime_is_standing_though_it_may_not_pass(session) -> None:
    """LM Studio runs inference here: 2 cpus for as long as its agent lives. A head
    only it holds back is one agents would be starving."""
    assert _standing(session, "lm studio run", cpus=2.0, memory_mb=4096).granted
    _heavy(session, "service", cpus=2.0)
    assert not _heavy(session, "head suite").granted, "2 + 2 + 4 is over 7"

    assert not _standing(session, "agent run").granted


def test_memory_the_head_needs_is_kept_though_it_is_free_now(session) -> None:
    _set_ceiling(session, CapacityPool.HOST, cpus=7, memory_mb=16_000, leases=6)
    for n in range(3):
        assert _heavy(session, f"work {n}", cpus=0.5, memory_mb=4096).granted
    assert not _heavy(session, "head", cpus=0.5, memory_mb=6000).granted

    # 3712 MB is free, room for four agents. Two pieces may stay: 8192 + 6000
    # leaves 1808.
    passed = [_standing(session, f"agent {n}").granted for n in range(3)]
    assert passed == [True, True, False]


def test_passing_claims_keep_their_own_order(session) -> None:
    """A 0.5-cpu orchestration that may not pass yet is not overtaken by a 0.25-cpu
    run behind it, or a stream of runs would starve it."""
    _blocked_line(session)
    agents = [_standing(session, f"agent {n}") for n in range(11)]
    assert all(agent.granted for agent in agents), "2.75 of the 3 that may pass"
    parent = _standing(session, "run orch_1", cpus=0.5, memory_mb=512, kind=PARENT)
    late = _standing(session, "late agent")

    assert not parent.granted
    assert not late.granted, "0.25 fits, and the orchestration asked first"
    agents[0].release()
    assert _lease(session, parent.lease_id).status is DockerLeaseStatus.HELD


def test_a_head_the_machines_load_holds_back_can_be_passed(session, monkeypatch) -> None:
    """The head fits the ledger and is held back by outside load alone; an agent
    beside it costs it nothing."""
    monkeypatch.setattr(settings, "host_load_aware", True)
    _standing(session, "agent 0")
    pool = load_pool(session, CapacityPool.HOST)
    pool.external_cpus = 7.0
    session.add(pool)
    session.commit()
    head = _heavy(session, "head suite")
    assert not head.granted

    assert _standing(session, "agent 1").granted


def test_two_drains_cannot_each_pass_the_last_claim_that_fits(session, isolated_db) -> None:
    """The permission is judged under the revision the booking is made against."""
    _blocked_line(session)
    for n in range(10):
        assert _standing(session, f"agent {n}").granted
    start = threading.Barrier(6)
    granted: list[bool] = []

    def claim(n: int) -> None:
        with Session(isolated_db) as mine:
            start.wait()
            granted.append(_standing(mine, f"racer {n}").granted)

    threads = [threading.Thread(target=claim, args=(n,)) for n in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join()

    session.expire_all()
    held = session.exec(
        select(DockerLease).where(
            DockerLease.status == DockerLeaseStatus.HELD, DockerLease.holder_kind == AGENT
        )
    ).all()
    assert len(held) == 12
    assert load_pool(session, CapacityPool.HOST).held_cpus == HEAVY + 3.0


# ---- the promise, over random machines ------------------------------------


def _fits(total: Room, ceiling: Room) -> bool:
    return total.cpus <= ceiling.cpus and total.memory_mb <= ceiling.memory_mb


@pytest.mark.parametrize("seed", range(40))
def test_no_pass_ever_moves_the_release_the_head_starts_on(session, seed) -> None:
    """Fill a random machine through the real ledger, let agents pass, then finish
    the running work in **every** order. The head starts on the same release as it
    would have with no agent having passed. Agents never finish, which is the
    worst they can do."""
    rng = random.Random(seed)
    ceiling = Room(rng.choice([4.0, 7.0, 9.0]), rng.choice([9_500, 16_000, 48_000]))
    _set_ceiling(
        session, CapacityPool.HOST, cpus=ceiling.cpus, memory_mb=ceiling.memory_mb, leases=6
    )
    before = [_standing(session, f"before {n}") for n in range(rng.randint(0, 4))]
    work = [
        Room(rng.choice([1.0, 2.0, 4.0]), rng.choice([1024, 4096, 8192]))
        for _ in range(rng.randint(1, 4))
    ]
    running = [
        piece
        for n, piece in enumerate(work)
        if _heavy(session, f"work {n}", cpus=piece.cpus, memory_mb=piece.memory_mb).granted
    ]
    session.expire_all()
    waiting = session.exec(
        select(DockerLease)
        .where(DockerLease.status == DockerLeaseStatus.WAITING)
        .order_by(DockerLease.position)
    ).first()
    if waiting is None:
        head = Room(min(4.0, ceiling.cpus), 8192)
        if _heavy(session, "head", cpus=head.cpus, memory_mb=head.memory_mb).granted:
            return  # nothing is blocked on this machine, so nothing can pass
    else:
        head = Room(waiting.cpus, waiting.memory_mb)

    agents = [
        _standing(session, f"late {n}", cpus=rng.choice([0.25, 0.5]))
        for n in range(rng.randint(1, 16))
    ]
    stood = sum((Room(0.25, 768) for claim in before if claim.granted), Room())
    standing = stood + sum((Room(c.cpus, c.memory_mb) for c in agents if c.granted), Room())

    def starts_on(order: tuple[Room, ...], held: Room) -> int | None:
        for done in range(len(order) + 1):
            if _fits(sum(order[done:], head + held), ceiling):
                return done
        return None

    for order in itertools.permutations(running):
        assert starts_on(order, standing) == starts_on(order, stood)


def test_the_promise_test_can_fail(session) -> None:
    """The oracle above is only worth its 40 seeds if a wrong rule trips it: a rule
    that asks only for free room would pass a fifth agent here."""
    two_stacks = [Room(2.0, 1024), Room(2.0, 1024)]
    common = {
        "ceiling": Room(7.0, 48_000),
        "work": two_stacks,
        "head": Room(4.0, 8192),
        "claim": Room(0.25, 768),
    }
    assert passing_leaves_head_alone(held=Room(4.75, 2048 + 3 * 768), **common)
    assert not passing_leaves_head_alone(held=Room(5.0, 2048 + 4 * 768), **common)


# ---- the line still reads right -------------------------------------------


def test_a_claim_that_passed_is_not_what_the_line_waits_behind(session) -> None:
    """Otherwise agents finishing every few minutes read as the line moving, and a
    hung suite holding it is never reported."""
    early = _standing(session, "early agent")
    running, head = _blocked_line(session)
    passed = _standing(session, "agent that passed")
    assert passed.granted

    ahead = leases_ahead(session, _lease(session, head.lease_id))

    assert set(ahead) == {running.lease_id, early.lease_id}


def test_work_booked_past_a_waiter_is_still_ahead_of_it(session) -> None:
    """A gate child is booked ahead of the line, and its ending frees real room."""
    parent = _standing(session, "run orch_1", cpus=0.5, memory_mb=512, kind=PARENT)
    _, head = _blocked_line(session)
    child = reserve_child(
        session,
        parent_lease_id=parent.lease_id,
        holder_label="gates implement→verify",
        footprint=DockerFootprint.CUSTOM,
        cpus=1.0,
        memory_mb=1024,
        pool=CapacityPool.HOST,
    )
    assert child.granted

    assert child.lease_id in leases_ahead(session, _lease(session, head.lease_id))


def test_an_agent_finishing_does_not_keep_a_stalled_child_waiting(session) -> None:
    """A gate child's retry gives up when nothing holding capacity finishes. Agents
    that started after it began must not count as progress."""
    _heavy(session, "hung suite")
    clock = itertools.count()
    attempts: list[int] = []

    def attempt() -> DockerReservation:
        attempts.append(1)
        return DockerReservation(state=docker_leases.DockerGrantState.REJECTED, message="full")

    def sleep(_seconds: float) -> None:
        # An agent starts, and the one before it has finished.
        claim = _standing(session, f"agent {len(attempts)}")
        session.expire_all()
        for lease in session.exec(
            select(DockerLease).where(
                DockerLease.status == DockerLeaseStatus.HELD,
                DockerLease.holder_kind == AGENT,
                DockerLease.id != claim.lease_id,
            )
        ).all():
            docker_leases.release_lease(
                session, lease.id, reason=DockerLeaseEndReason.RUN_COMPLETED
            )
        assert len(attempts) < 100, "the retry never gave up"

    retry_while_pool_moves(
        session,
        attempt,
        retryable=lambda _reservation: True,
        waiting_for="gate child",
        stall_seconds=30,
        clock=lambda: float(next(clock)),
        sleep=sleep,
    )


def test_an_agent_held_when_the_wait_began_still_counts(session) -> None:
    """It holds capacity the child may be waiting for, and it is one of a fixed few."""
    agent = _standing(session, "early agent")
    now = [0.0]
    seen: list[float] = []

    def attempt() -> DockerReservation:
        seen.append(now[0])
        return DockerReservation(state=docker_leases.DockerGrantState.REJECTED, message="full")

    def sleep(_seconds: float) -> None:
        now[0] += 1
        if now[0] == 8:
            agent.release()

    retry_while_pool_moves(
        session,
        attempt,
        retryable=lambda _reservation: True,
        waiting_for="gate child",
        stall_seconds=10,
        clock=lambda: now[0],
        sleep=sleep,
    )

    assert seen[-1] == 18


def test_work_that_starts_during_the_wait_counts_when_it_ends(session) -> None:
    now = [0.0]
    seen: list[float] = []
    started: list[DockerReservation] = []

    def attempt() -> DockerReservation:
        seen.append(now[0])
        return DockerReservation(state=docker_leases.DockerGrantState.REJECTED, message="full")

    def sleep(_seconds: float) -> None:
        now[0] += 1
        if now[0] == 3:
            started.append(_heavy(session, "short suite"))
        if now[0] == 8:
            started[0].release()

    retry_while_pool_moves(
        session,
        attempt,
        retryable=lambda _reservation: True,
        waiting_for="gate child",
        stall_seconds=10,
        clock=lambda: now[0],
        sleep=sleep,
    )

    assert seen[-1] == 18


def _held_for(session: Session, *claims: tuple[DockerReservation, int]) -> None:
    """Every hold here lasts an hour; each of these has held for so many seconds."""
    _history(session, footprint=DockerFootprint.CUSTOM, held_seconds=3600)
    for claim, held in claims:
        lease = _lease(session, claim.lease_id)
        lease.granted_at = NOW - timedelta(seconds=held)
        session.add(lease)
        session.commit()


def test_the_projection_lets_a_claim_pass_when_the_drain_would(session) -> None:
    """A 2-cpu stack ends first and frees room for the waiting agent to pass the
    head, which waits for the suite."""
    suite = _heavy(session, "suite")
    stack = _heavy(session, "stack", cpus=2.0, memory_mb=1024)
    head = _heavy(session, "head suite")
    agents = [_standing(session, f"agent {n}") for n in range(4)]
    assert all(agent.granted for agent in agents), "7 of 7 cpus held"
    waiting = _standing(session, "late agent")
    assert not head.granted and not waiting.granted
    _held_for(session, (suite, 600), (stack, 3000), *((agent, 0) for agent in agents))

    estimates = estimate_waits(session, now=NOW)

    assert estimates[waiting.lease_id].seconds == pytest.approx(600, abs=2)
    assert estimates[waiting.lease_id].basis is DockerWaitBasis.HISTORY
    assert estimates[head.lease_id].seconds == pytest.approx(3000, abs=2)


def test_a_claim_projected_past_the_head_is_projected_once(session) -> None:
    """Another holder ends between its start and the head's. It must not be started
    again then, or it reads as waiting longer and books its room twice."""
    suite = _heavy(session, "suite")
    stack = _heavy(session, "stack", cpus=2.0, memory_mb=1024)
    service = _heavy(session, "service", cpus=1.0, memory_mb=1024)
    assert not _heavy(session, "head suite").granted
    waiting = _standing(session, "agent run")
    assert not waiting.granted
    _held_for(session, (stack, 3000), (service, 2000), (suite, 600))

    estimates = estimate_waits(session, now=NOW)

    assert estimates[waiting.lease_id].seconds == 600


def test_the_projection_keeps_the_passing_claims_order(session) -> None:
    suite = _heavy(session, "suite")
    agents = [_standing(session, f"agent {n}") for n in range(11)]
    assert all(agent.granted for agent in agents)
    assert not _heavy(session, "head suite").granted
    parent = _standing(session, "run orch_1", cpus=0.5, memory_mb=512, kind=PARENT)
    late = _standing(session, "late agent")
    assert not parent.granted and not late.granted, "0.25 of the 3 cpus is left"
    _held_for(session, (suite, 0), (agents[0], 3000), *((agent, 0) for agent in agents[1:]))

    estimates = estimate_waits(session, now=NOW)

    assert estimates[parent.lease_id].seconds == 600
    assert estimates[late.lease_id].seconds == 3600


def test_the_projection_knows_a_claim_it_started_stands(session) -> None:
    """Once LM Studio is projected to run, it and the stack hold the head back, so no
    agent is projected past it."""
    suite = _heavy(session, "suite")
    stack = _heavy(session, "stack", cpus=2.0, memory_mb=1024)
    service = _heavy(session, "service", cpus=1.0, memory_mb=1024)
    lm_studio = _standing(session, "lm studio run", cpus=2.0, memory_mb=4096)
    assert not lm_studio.granted
    head = _heavy(session, "head suite")
    waiting = _standing(session, "agent run")
    assert not waiting.granted
    _held_for(session, (suite, 3000), (service, 3600 - 601), (stack, 600))

    estimates = estimate_waits(session, now=NOW)

    assert estimates[lm_studio.lease_id].seconds == 600
    assert estimates[head.lease_id].seconds == 3000
    assert estimates[waiting.lease_id].seconds == 3000


def test_the_projection_reads_running_standing_claims_as_standing(session) -> None:
    service = _heavy(session, "service", cpus=2.0, memory_mb=1024)
    lm_studio = _standing(session, "lm studio run", cpus=2.0, memory_mb=4096)
    head = _heavy(session, "head suite")
    waiting = _standing(session, "agent run")
    assert not head.granted and not waiting.granted
    _held_for(session, (service, 600), (lm_studio, 1200))

    estimates = estimate_waits(session, now=NOW)

    assert estimates[waiting.lease_id].seconds == estimates[head.lease_id].seconds == 2400
