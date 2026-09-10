"""Two claimants, one pool, and only one of them may win.

This is the test the design exists for. Every other admission test would pass
against a `SELECT SUM(...)` followed by an `INSERT` — the select-then-mutate
shape `claim_free_slot` was written to remove — because a single-threaded test
never interleaves the read and the write. Two sessions do.

**Which test does the work, measured rather than assumed.** `_claim_capacity`
was mutated into a check-then-act implementation and this module re-run:
`test_concurrent_claimants_never_exceed_the_ceiling` failed, and
`test_two_sessions_reading_room_do_not_both_get_it` passed. So the threaded
fan-in is the control here; the two-session test proves the weaker property that
one session's uncommitted view does not leak into another's claim, which is
worth pinning but is not atomicity. Do not delete the threaded test on the
grounds that the other one covers it — it does not.
"""

from __future__ import annotations

import threading

import pytest
from loregarden.models.domain import (
    DockerCeilingSource,
    DockerFootprint,
    DockerGrantState,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases
from sqlmodel import Session, func, select


def _set_ceiling(session: Session, *, cpus: float, memory_mb: int, leases: int) -> None:
    pool = docker_leases.load_pool(session)
    pool.ceiling_cpus = cpus
    pool.ceiling_memory_mb = memory_mb
    pool.ceiling_leases = leases
    pool.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(pool)
    session.commit()


def _reserve(session: Session, label: str, *, cpus: float, memory_mb: int):
    return docker_leases.reserve(
        session,
        holder_label=label,
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=memory_mb,
    )


def test_two_sessions_reading_room_do_not_both_get_it(isolated_db) -> None:
    """Each session claims 60% of the pool. Both see room; one may have it.

    Sequential by construction — the reserves do not interleave — so this
    catches a claim that reads through a stale cross-session snapshot, not a
    check-then-act race. See the module docstring: the threaded test is what
    falsifies that.
    """
    with Session(isolated_db) as setup:
        _set_ceiling(setup, cpus=10.0, memory_mb=10240, leases=10)

    with Session(isolated_db) as first, Session(isolated_db) as second:
        # Both read the pool as empty before either writes — the interleaving a
        # check-then-act implementation cannot survive.
        assert docker_leases.load_pool(first).held_cpus == 0.0
        assert docker_leases.load_pool(second).held_cpus == 0.0

        one = _reserve(first, "first", cpus=6.0, memory_mb=6144)
        two = _reserve(second, "second", cpus=6.0, memory_mb=6144)

    granted = [r for r in (one, two) if r.state is DockerGrantState.GRANTED]
    queued = [r for r in (one, two) if r.state is DockerGrantState.QUEUED]
    assert len(granted) == 1, "the pool cannot hold both"
    assert len(queued) == 1, "the loser waits its turn rather than failing"

    with Session(isolated_db) as check:
        pool = docker_leases.load_pool(check)
        assert pool.held_cpus == 6.0
        assert pool.held_cpus <= pool.ceiling_cpus


@pytest.mark.parametrize("claimants", [8])
def test_concurrent_claimants_never_exceed_the_ceiling(isolated_db, claimants: int) -> None:
    """Under real contention, four invariants — and one deliberate non-invariant.

    Holds:

    - **Safety.** No more capacity is held than the ceiling allows.
    - **Agreement.** The pool's running totals equal the ledger they index. This
      is the one that caught the original defect: two drains booking capacity
      for the same waiter gave `held_count=3` with two HELD rows, leaking a unit
      of capacity permanently while every single-threaded test still passed.
    - **No over-reporting.** Every reservation that says GRANTED names a lease
      that is really HELD. Reversing the order of the two claims made this fail
      instead, which is strictly worse: it tells a caller to start containers on
      capacity it does not hold.
    - **Liveness.** As many leases are granted as fit. Not "at least one".

    Does NOT hold, on purpose: that the *number of reservations reporting*
    GRANTED equals the number granted. A claimant can return QUEUED and have its
    lease promoted an instant later by a peer's drain. The state is right and the
    answer is merely stale, which is exactly what the poll protocol is for — see
    the test below.
    """
    with Session(isolated_db) as setup:
        _set_ceiling(setup, cpus=3.0, memory_mb=3072, leases=3)

    results: list = []
    lock = threading.Lock()
    start = threading.Barrier(claimants)

    def claim(index: int) -> None:
        start.wait(timeout=10)
        with Session(isolated_db) as session:
            reservation = _reserve(session, f"claimant-{index}", cpus=1.0, memory_mb=1024)
        with lock:
            results.append(reservation)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(claimants)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)
    assert not any(thread.is_alive() for thread in threads), "a claimant deadlocked"
    assert len(results) == claimants, "a reservation raised instead of queueing"

    with Session(isolated_db) as check:
        pool = docker_leases.load_pool(check)
        held = check.exec(
            select(DockerLease).where(DockerLease.status == DockerLeaseStatus.HELD)
        ).all()
        held_ids = {lease.id for lease in held}
        held_cpus = check.exec(
            select(func.coalesce(func.sum(DockerLease.cpus), 0)).where(
                DockerLease.status == DockerLeaseStatus.HELD
            )
        ).one()

    assert len(held) == 3, f"3 fit; {len(held)} are held"
    assert pool.held_cpus <= pool.ceiling_cpus
    assert pool.held_count <= pool.ceiling_leases
    assert pool.held_cpus == held_cpus, "the pool disagrees with the ledger it indexes"
    assert pool.held_count == len(held)

    claimed_granted = {r.lease_id for r in results if r.state is DockerGrantState.GRANTED}
    assert claimed_granted <= held_ids, (
        "a reservation reported GRANTED for a lease that is not held — the caller "
        "would start containers on capacity it does not have"
    )
    assert all(r.state in (DockerGrantState.GRANTED, DockerGrantState.QUEUED) for r in results), (
        "contention must queue, never refuse"
    )


def test_a_queued_claimant_whose_lease_a_peer_promoted_can_see_it(isolated_db) -> None:
    """What makes the staleness above tolerable rather than a lie.

    A reservation's answer is a snapshot; the lease is the truth. A caller told
    QUEUED polls, and the poll reports what actually happened — so the protocol
    is correct even when the immediate answer is behind.
    """
    with Session(isolated_db) as setup:
        _set_ceiling(setup, cpus=1.0, memory_mb=1024, leases=1)
        holder = _reserve(setup, "holder", cpus=1.0, memory_mb=1024)
        waiter = _reserve(setup, "waiter", cpus=1.0, memory_mb=1024)
        assert waiter.state is DockerGrantState.QUEUED

    # A peer releases; the drain that follows promotes the waiter without the
    # waiter's own session hearing about it.
    with Session(isolated_db) as peer:
        docker_leases.release_lease(peer, holder.lease_id)

    with Session(isolated_db) as poller:
        lease = poller.get(DockerLease, waiter.lease_id)
        assert lease.status is DockerLeaseStatus.HELD
        assert lease.expires_at is not None, "a held lease always has an expiry to renew against"


def test_concurrent_waiters_get_distinct_positions(isolated_db) -> None:
    """Two waiters handed the same position means the queue has two heads, and
    a drain that walks in position order cannot then be head-of-line."""
    with Session(isolated_db) as setup:
        _set_ceiling(setup, cpus=1.0, memory_mb=1024, leases=1)
        _reserve(setup, "holder", cpus=1.0, memory_mb=1024)

    start = threading.Barrier(6)

    def claim(index: int) -> None:
        start.wait(timeout=10)
        with Session(isolated_db) as session:
            _reserve(session, f"waiter-{index}", cpus=0.5, memory_mb=128)

    threads = [threading.Thread(target=claim, args=(i,)) for i in range(6)]
    for thread in threads:
        thread.start()
    for thread in threads:
        thread.join(timeout=30)

    with Session(isolated_db) as check:
        positions = [
            lease.position
            for lease in check.exec(
                select(DockerLease).where(DockerLease.status == DockerLeaseStatus.WAITING)
            ).all()
        ]
    assert len(positions) == 6
    assert len(set(positions)) == len(positions), f"duplicate queue positions: {sorted(positions)}"
