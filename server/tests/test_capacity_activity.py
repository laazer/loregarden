"""Telling a command that hung from one that is only quiet.

- CPU is counted over the whole process tree, per process, so a worker that
  exits does not hide the work of the rest;
- a lease looks hung only when CPU and progress have both been flat;
- a lease nothing recent measured has no reading, rather than a hung one;
- the board and the line waiting behind it both say so.
"""

from __future__ import annotations

import os
import subprocess
import sys
import time
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.db.versions.lease_cpu_activity import m_lease_cpu_activity
from loregarden.models.domain import (
    CapacityPool,
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseStatus,
)
from loregarden.services import docker_leases
from loregarden.services.capacity_activity import (
    HUNG_AFTER_SECONDS,
    SAMPLE_INTERVAL_SECONDS,
    CpuSync,
    lease_activity,
    parse_cpu_time,
    parse_process_table,
    read_tree_cpu,
    tree_cpu_seconds,
)
from loregarden.services.capacity_run import (
    CapacityQueueStalled,
    CapacityRequest,
    acquire,
    run_holding,
)
from loregarden.services.docker_board import capacity_status
from loregarden.services.docker_ledger import load_pool
from sqlalchemy import create_engine, text
from sqlalchemy.exc import OperationalError
from sqlmodel import Session, select

NOW = datetime(2026, 11, 25, 14, 0, tzinfo=timezone.utc)


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host")
def host_fixture(session):
    with (
        mock.patch.object(settings, "host_capacity_cpus", 4.0),
        mock.patch.object(settings, "host_capacity_memory_mb", 8192),
        mock.patch.object(settings, "host_capacity_max_leases", 4),
    ):
        row = load_pool(session, CapacityPool.HOST)
        row.ceiling_cpus, row.ceiling_memory_mb, row.ceiling_leases = 4.0, 8192, 4
        row.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
        session.add(row)
        session.commit()
        yield row


def _hold(session, label: str = "suite", *, cpus: float = 1.0) -> DockerLease:
    claim = docker_leases.reserve(
        session,
        holder_label=label,
        footprint=DockerFootprint.CUSTOM,
        cpus=cpus,
        memory_mb=512,
        pool=CapacityPool.HOST,
    )
    return session.get(DockerLease, claim.lease_id)


def _hung(session, label: str, *, cpus: float = 1.0) -> DockerLease:
    lease = _hold(session, label, cpus=cpus)
    now = datetime.now(timezone.utc)
    lease.cpu_sampled_at = now
    lease.cpu_active_at = now - timedelta(minutes=22)
    session.add(lease)
    session.commit()
    return lease


@pytest.mark.parametrize(
    ("printed", "seconds"),
    [
        ("0:00.04", 0.04),  # macOS
        ("25:17.94", 1517.94),
        ("123:05.50", 7385.5),
        ("00:00:07", 7.0),  # Linux
        ("2-03:04:05", 183845.0),
    ],
)
def test_cpu_time_is_read_as_ps_prints_it_on_either_platform(printed, seconds) -> None:
    assert parse_cpu_time(printed) == pytest.approx(seconds)


def test_the_tree_is_every_descendant_and_not_the_root_or_its_siblings() -> None:
    table = parse_process_table(
        "  10     1   5:00.00\n  20    10   0:01.00\n  30    20   0:02.50\n  40     1   9:00.00\n\n"
    )
    assert tree_cpu_seconds(table, 10) == {20: 1.0, 30: 2.5}


def test_a_busy_grandchild_is_seen_by_the_real_ps() -> None:
    spin = "import time\nend = time.time() + 30\nwhile time.time() < end: pass"
    parent = subprocess.Popen(["sh", "-c", f"{sys.executable} -c '{spin}'; true"])  # noqa: S603, S607
    try:
        deadline = time.monotonic() + 25
        used = 0.0
        while used < 0.2 and time.monotonic() < deadline:
            used = sum(read_tree_cpu(parent.pid).values())
        assert used >= 0.2
        assert parent.pid in read_tree_cpu(os.getpid())
    finally:
        for pid in read_tree_cpu(parent.pid):
            os.kill(pid, 9)
        parent.kill()
        parent.wait()


class _Samples:
    """A scripted process tree and the clock it is read on."""

    def __init__(self, engine, lease_id: str) -> None:
        self.now = 1000.0
        self.cpu: dict[int, float] = {}
        self.error: Exception | None = None
        self.lines: list[str] = []
        self.sync = CpuSync(
            lambda: Session(engine),
            lease_id,
            self.lines.append,
            root=1,
            read=self._read,
            clock=lambda: self.now,
        )

    def _read(self, _root: int) -> dict[int, float]:
        if self.error is not None:
            raise self.error
        return dict(self.cpu)

    def take(self, cpu: dict[int, float], *, after: float = SAMPLE_INTERVAL_SECONDS) -> None:
        self.now += after
        self.cpu = cpu
        self.sync.sample()


def _stamps(session, lease_id: str, samples: _Samples, cpu, **kw) -> tuple[bool, bool]:
    """Whether a sample moved (sampled_at, active_at)."""
    session.expire_all()
    lease = session.get(DockerLease, lease_id)
    before = (lease.cpu_sampled_at, lease.cpu_active_at)
    samples.take(cpu, **kw)
    session.expire_all()
    lease = session.get(DockerLease, lease_id)
    return lease.cpu_sampled_at != before[0], lease.cpu_active_at != before[1]


@pytest.mark.parametrize(
    ("second", "kwargs", "active"),
    [
        ({7: 10.0, 8: 20.0}, {}, False),  # nothing ran
        ({7: 10.0, 8: 20.9}, {}, False),  # a poller: 1.5% of a cpu
        ({7: 10.0, 8: 24.0}, {}, True),  # 6.7%
        ({8: 24.0}, {}, True),  # a worker exited, the other worked
        ({8: 20.0}, {}, False),  # a worker exited, the other did not
        ({7: 10.0, 8: 20.0, 9: 5.0}, {}, True),  # a new worker
        ({7: 10.0, 8: 20.0}, {"after": 3600.0}, True),  # asleep: not watched, so not idle
    ],
)
def test_working_is_cpu_used_since_the_last_sample(
    isolated_db, session, host, second, kwargs, active
) -> None:
    lease_id = _hold(session).id
    samples = _Samples(isolated_db, lease_id)
    assert _stamps(session, lease_id, samples, {7: 10.0, 8: 20.0}) == (True, True)
    assert _stamps(session, lease_id, samples, second, **kwargs) == (True, active)


def test_a_failing_sample_is_said_once_and_leaves_the_lease_unmeasured(
    isolated_db, session, host
) -> None:
    lease = _hold(session)
    samples = _Samples(isolated_db, lease.id)
    samples.error = subprocess.TimeoutExpired("ps", 15)
    samples.take({})
    samples.take({})
    session.expire_all()
    assert session.get(DockerLease, lease.id).cpu_sampled_at is None
    assert len([line for line in samples.lines if "could not sample" in line]) == 1


def test_a_locked_database_is_said_and_does_not_stop_the_sampling(
    isolated_db, session, host
) -> None:
    lease_id = _hold(session).id
    samples = _Samples(isolated_db, lease_id)
    locked = OperationalError("UPDATE", {}, Exception("database is locked"))
    with mock.patch("loregarden.services.capacity_activity.record_cpu_sample", side_effect=locked):
        samples.take({7: 1.0})
    assert len(samples.lines) == 1
    assert _stamps(session, lease_id, samples, {7: 30.0}) == (True, True)


def _lease(**fields) -> DockerLease:
    given = {
        "status": DockerLeaseStatus.HELD,
        "pool": CapacityPool.HOST,
        "cpu_sampled_at": NOW - timedelta(seconds=20),
        "cpu_active_at": NOW - timedelta(seconds=HUNG_AFTER_SECONDS + 20),
    }
    return DockerLease(**(given | fields))


def test_no_cpu_and_no_progress_for_long_enough_looks_hung() -> None:
    reading = lease_activity(_lease(), now=NOW)
    assert (reading.idle_seconds, reading.looks_hung) == (HUNG_AFTER_SECONDS, True)


@pytest.mark.parametrize(
    "fields",
    [
        {"cpu_active_at": NOW - timedelta(seconds=HUNG_AFTER_SECONDS + 19)},
        # Long enough ago by the clock, but nobody has looked since: 70s late.
        {
            "cpu_active_at": NOW - timedelta(seconds=HUNG_AFTER_SECONDS + 60),
            "cpu_sampled_at": NOW - timedelta(seconds=70),
        },
        # Waiting on something else, and saying so.
        {"progress_at": NOW - timedelta(seconds=60)},
        # 2026-11-25: the report is 20 minutes old and the suite is working.
        {"cpu_active_at": NOW, "progress_at": NOW - timedelta(minutes=20)},
    ],
)
def test_either_cpu_or_progress_is_enough_to_be_working(fields) -> None:
    assert lease_activity(_lease(**fields), now=NOW).looks_hung is False


@pytest.mark.parametrize(
    "fields",
    [
        {"cpu_sampled_at": None, "cpu_active_at": None},
        # The sampler stopped: an hour without a sample is not an hour idle.
        {"cpu_sampled_at": NOW - timedelta(hours=1)},
        # Its work is in the daemon's containers, outside the tree.
        {"pool": CapacityPool.DOCKER},
        {"status": DockerLeaseStatus.WAITING},
        {"status": DockerLeaseStatus.RELEASED},
    ],
)
def test_a_lease_nothing_measures_has_no_reading(fields) -> None:
    assert lease_activity(_lease(**fields), now=NOW) is None


def test_the_board_carries_the_reading(session, host) -> None:
    _hung(session, "deadlocked suite")
    _hold(session, "unmeasured")
    board = {
        row["holder_label"]: row["activity"]
        for row in capacity_status(session, measure_if_unknown=False)["holders"]
    }
    assert board["unmeasured"] is None
    assert board["deadlocked suite"]["looks_hung"] is True
    assert 1320 <= board["deadlocked suite"]["idle_seconds"] <= 1330


def test_a_waiter_is_told_first_which_holder_looks_hung(session, host) -> None:
    _hold(session, "older and working", cpus=1.0)
    _hung(session, "deadlocked suite", cpus=3.0)
    lines: list[str] = []
    clock = [0.0]

    def sleep(seconds: float) -> None:
        clock[0] += seconds

    request = CapacityRequest(label="next", cpus=2.0, memory_mb=512, stall_seconds=120.0)
    with pytest.raises(CapacityQueueStalled, match="'deadlocked suite' has held .* may be hung"):
        acquire(session, request, report=lines.append, sleep=sleep, clock=lambda: clock[0])

    ahead = [line for line in lines if "ahead: '" in line]
    assert "deadlocked suite" in ahead[0]
    assert "no CPU use or progress for 22m" in ahead[0]
    assert "hung" not in ahead[1]


def test_a_held_command_is_sampled_and_a_sleeping_one_goes_idle(isolated_db, session, host) -> None:
    """End to end over the real `ps`: `sleep` stays in one sample, then is idle."""
    with mock.patch("loregarden.services.capacity_run.CpuSync") as sync:
        sync.side_effect = lambda factory, lease_id, report: CpuSync(
            factory, lease_id, report, interval=0.5
        )
        code = run_holding(
            lambda: Session(isolated_db),
            CapacityRequest(label="sleeper", cpus=1.0, memory_mb=512),
            # Five samples or so: the first counts as working, the rest see none.
            ["sleep", "3"],
            report=lambda _line: None,
        )
    assert code == 0
    (lease,) = session.exec(select(DockerLease)).all()
    assert lease.cpu_sampled_at is not None
    assert lease.cpu_active_at < lease.cpu_sampled_at


def test_the_migration_adds_the_columns_and_reruns_cleanly(tmp_path) -> None:
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as conn:
        conn.execute(text("CREATE TABLE docker_leases (id TEXT PRIMARY KEY)"))
        conn.execute(text("INSERT INTO docker_leases (id) VALUES ('a')"))
        m_lease_cpu_activity(conn)
        m_lease_cpu_activity(conn)
        row = conn.execute(text("SELECT * FROM docker_leases")).mappings().one()
    assert (row["cpu_sampled_at"], row["cpu_active_at"]) == (None, None)
