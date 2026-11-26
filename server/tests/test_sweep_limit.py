"""At most one sweep per repository holds capacity; nothing else waits on that rule.

lg-build-verification-2806. Run against the real granter (`reserve`,
`drain_waiters`, `release_lease`) on a host with room to spare, so every wait
seen here is the sweep rule and not the machine being full.
"""

from __future__ import annotations

import os

import pytest
from loregarden.models.domain import (
    CapacityClass,
    CapacityPool,
    DockerCeilingSource,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.services.capacity_run import CapacityRequest, acquire
from loregarden.services.docker_leases import drain_waiters, release_lease, reserve
from loregarden.services.docker_ledger import load_pool
from sqlmodel import Session

REPO = "/work/loregarden/.git"
OTHER_REPO = "/work/blobert/.git"


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host")
def host_fixture(session):
    """A 16-cpu / 32 GB host with eight slots: room for every claim below at once."""
    row = load_pool(session, CapacityPool.HOST)
    row.ceiling_cpus = 16.0
    row.ceiling_memory_mb = 32768
    row.ceiling_leases = 8
    row.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
    session.add(row)
    session.commit()
    return row


def _claim(session, label: str, *, sweep: bool, repo: str = REPO, cpus: float = 2.0):
    return reserve(
        session,
        holder_label=f"{label} · pid {os.getpid()}",
        cpus=cpus,
        memory_mb=1024,
        holder_pid=os.getpid(),
        pool=CapacityPool.HOST,
        capacity_class=CapacityClass.SWEEP if sweep else CapacityClass.ORDINARY,
        repo_key=repo if sweep else "",
    )


def _status(session, lease_id: str) -> DockerLeaseStatus:
    session.expire_all()
    return session.get(DockerLease, lease_id).status


def test_a_second_sweep_from_the_same_repo_waits_while_one_holds(session, host) -> None:
    first = _claim(session, "timing sweep · claude/a", sweep=True)
    second = _claim(session, "mutation sweep · claude/b", sweep=True)

    assert first.granted
    assert not second.granted, "two sweeps of one repository held at once"

    release_lease(session, first.lease_id)
    drain_waiters(session)

    assert _status(session, second.lease_id) is DockerLeaseStatus.HELD


def test_sweeps_from_different_repos_do_not_wait_on_each_other(session, host) -> None:
    assert _claim(session, "sweep", sweep=True, repo=REPO).granted
    assert _claim(session, "sweep", sweep=True, repo=OTHER_REPO).granted


def test_a_push_behind_a_blocked_sweep_is_granted(session, host) -> None:
    """The sweep rule passes a blocked sweep over; it never becomes a head others wait on."""
    _claim(session, "timing sweep", sweep=True)
    blocked = _claim(session, "mutation sweep", sweep=True)
    push = _claim(session, "pre-push server-tests", sweep=False, cpus=4.0)
    landing = _claim(session, "land verification", sweep=False, cpus=4.0)

    assert not blocked.granted
    assert push.granted, "a push waited on the sweep limit"
    assert landing.granted, "a landing waited on the sweep limit"


def test_sweeps_of_one_repo_are_granted_in_order_and_one_at_a_time(session, host) -> None:
    """Room for both at once still grants one: the later waits on the earlier waiting one."""
    filler = _claim(session, "pre-push", sweep=False, cpus=16.0)
    first = _claim(session, "sweep one", sweep=True)
    second = _claim(session, "sweep two", sweep=True)
    assert filler.granted
    assert not first.granted and not second.granted

    release_lease(session, filler.lease_id)
    drain_waiters(session)

    assert _status(session, first.lease_id) is DockerLeaseStatus.HELD
    assert _status(session, second.lease_id) is DockerLeaseStatus.WAITING


def test_the_waiting_sweep_names_the_holders_label_branch_and_pid(session, host) -> None:
    holder = reserve(
        session,
        holder_label="mutation sweep · claude/capacity-sweep · pid 4242",
        cpus=2.0,
        memory_mb=1024,
        holder_pid=os.getpid(),  # alive, so no reap pass takes it
        pool=CapacityPool.HOST,
        capacity_class=CapacityClass.SWEEP,
        repo_key=REPO,
    )
    assert holder.granted
    said: list[str] = []

    def stop_after_first_poll(_seconds: float) -> None:
        raise KeyboardInterrupt

    with pytest.raises(KeyboardInterrupt):
        acquire(
            session,
            CapacityRequest(
                label="timing sweep",
                cpus=2.0,
                memory_mb=1024,
                capacity_class=CapacityClass.SWEEP,
                repo_key=REPO,
            ),
            report=said.append,
            sleep=stop_after_first_poll,
        )

    (line,) = [line for line in said if "sweep waiting" in line]
    assert "'mutation sweep'" in line
    assert "claude/capacity-sweep" in line
    assert f"pid {os.getpid()}" in line  # the lease's own pid, which is what holds it


def test_a_sweep_must_name_its_repository() -> None:
    with pytest.raises(ValueError, match="repo_key"):
        CapacityRequest(label="sweep", capacity_class=CapacityClass.SWEEP)
