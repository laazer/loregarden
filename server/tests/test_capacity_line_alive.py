"""A holder that is measurably working keeps the line behind it alive.

On 2026-11-25 a push at the head of the line lost its place after 3h50m because
nothing ahead had *ended* for an hour. Both holders were working, and finished
within two hours of it giving up.

- A holder seen working restarts the stall clock, as of when it was last seen.
- One that looks hung, or that nothing measures, does not: that line still lasts
  `stall_seconds`.
- A holder that spins forever counts for the first four hours of its hold.
"""

from __future__ import annotations

from collections.abc import Callable
from datetime import datetime, timedelta
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.models.domain import (
    CapacityPool,
    DockerCeilingSource,
    DockerFootprint,
    DockerLease,
    DockerLeaseEndReason,
)
from loregarden.models.domain.enums import utcnow
from loregarden.services import capacity_run, docker_leases
from loregarden.services.capacity_activity import WORKING_HOLD_COUNTS_SECONDS
from loregarden.services.capacity_run import (
    DEFAULT_STALL_SECONDS,
    CapacityQueueStalled,
    CapacityRequest,
    acquire,
)
from loregarden.services.docker_ledger import load_pool
from sqlmodel import Session, select

STALL = DEFAULT_STALL_SECONDS
#: The slack a stall is allowed: the waiter looks again at most this much later.
POLL_SLACK = 60.0
#: How often the waiter looks, in fake time. Each poll is ~20 ms of real ledger
#: work, so a five-hour line at 45 s was 400 polls and 7 s on CI, over the
#: per-test ceiling. Cadence is not under test: every event below lands on a
#: multiple of this, each holder is sampled just before the waiter reads it, and a
#: stall still lands on its second because the waiter never sleeps past it.
POLL = 300
#: Past this nothing under test should still be in line.
TOO_LONG = 24 * 3600.0

#: How a holder's samplers stamp its lease, given the fake wall clock.
Stamp = Callable[[DockerLease, datetime], None]


@pytest.fixture(name="session")
def session_fixture(isolated_db):
    with Session(isolated_db) as session:
        yield session


@pytest.fixture(name="host")
def host_fixture(session):
    """Four cpus on either pool. Pinned in settings too: the waiter's reap pass re-derives them."""
    with (
        mock.patch.object(settings, "host_capacity_cpus", 4.0),
        mock.patch.object(settings, "host_capacity_memory_mb", 8192),
        mock.patch.object(settings, "host_capacity_max_leases", 4),
    ):
        for pool in (CapacityPool.HOST, CapacityPool.DOCKER):
            row = load_pool(session, pool)
            row.ceiling_cpus, row.ceiling_memory_mb, row.ceiling_leases = 4.0, 8192, 4
            row.ceiling_source = DockerCeilingSource.CONFIG_OVERRIDE
            session.add(row)
        session.commit()
        yield


class _Line:
    """The line in fake time, with each holder's sampler stamping its lease as it passes."""

    def __init__(self, session: Session) -> None:
        self.session = session
        self.began = utcnow()
        self.now = 0.0
        self.said: list[str] = []
        self._stamps: dict[str, Stamp] = {}
        self._ends: dict[str, float] = {}

    def wall(self) -> datetime:
        return self.began + timedelta(seconds=self.now)

    def hold(
        self,
        label: str,
        stamp: Stamp,
        *,
        cpus: float = 4.0,
        ends: float | None = None,
        pool: CapacityPool = CapacityPool.HOST,
    ) -> DockerLease:
        claim = docker_leases.reserve(
            self.session,
            holder_label=label,
            footprint=DockerFootprint.CUSTOM,
            cpus=cpus,
            memory_mb=512,
            pool=pool,
        )
        assert claim.granted
        self._stamps[claim.lease_id] = stamp
        if ends is not None:
            self._ends[claim.lease_id] = ends
        self._sample()
        return self.session.get(DockerLease, claim.lease_id)

    def _sample(self) -> None:
        for lease_id, stamp in self._stamps.items():
            lease = self.session.get(DockerLease, lease_id)
            stamp(lease, self.wall())
            self.session.add(lease)
        self.session.commit()

    def sleep(self, seconds: float) -> None:
        self.now += seconds
        assert self.now < TOO_LONG, "still waiting"
        for lease_id in [held for held, ends in self._ends.items() if self.now >= ends]:
            del self._ends[lease_id], self._stamps[lease_id]
            docker_leases.release_lease(self.session, lease_id)
        self._sample()

    def wait(self, *, stall_seconds: float = STALL):
        with (
            mock.patch.object(capacity_run, "utcnow", self.wall),
            mock.patch.object(capacity_run, "poll_interval_for", return_value=POLL),
        ):
            return acquire(
                self.session,
                CapacityRequest(label="next", cpus=4.0, memory_mb=512, stall_seconds=stall_seconds),
                report=self.said.append,
                sleep=self.sleep,
                clock=lambda: self.now,
            )

    def wait_until_stalled(self) -> str:
        with pytest.raises(CapacityQueueStalled) as stalled:
            self.wait()
        self.session.expire_all()
        mine = self.session.exec(
            select(DockerLease).where(DockerLease.holder_label.startswith("next"))
        ).one()
        assert mine.end_reason is DockerLeaseEndReason.ABANDONED
        return str(stalled.value)

    def assert_stalled_at(self, seconds: float) -> None:
        assert seconds <= self.now < seconds + POLL_SLACK


@pytest.fixture(name="line")
def line_fixture(session, host) -> _Line:
    return _Line(session)


def _unmeasured(_lease: DockerLease, _wall: datetime) -> None:
    """An older wrapper: it renews its lease and samples nothing."""


def _busy(lease: DockerLease, wall: datetime) -> None:
    lease.cpu_sampled_at = lease.cpu_active_at = wall


def _busy_until(line: _Line, seconds: float) -> Stamp:
    """Works, then hangs; its sampler keeps looking."""

    def stamp(lease: DockerLease, wall: datetime) -> None:
        lease.cpu_sampled_at = wall
        lease.cpu_active_at = min(wall, line.began + timedelta(seconds=seconds))

    return stamp


def _sampled_until(line: _Line, seconds: float) -> Stamp:
    """Works, and is watched working, until its sampler stops."""

    def stamp(lease: DockerLease, wall: datetime) -> None:
        if wall <= line.began + timedelta(seconds=seconds):
            _busy(lease, wall)

    return stamp


def _hung(line: _Line) -> Stamp:
    """Idle since 22 minutes before anyone queued behind it."""
    return _busy_until(line, -22 * 60)


def _reporting(line: _Line) -> Stamp:
    """Uses no CPU of its own and says how far it has got."""

    def stamp(lease: DockerLease, wall: datetime) -> None:
        _hung(line)(lease, wall)
        lease.progress_at = wall

    return stamp


def test_a_busy_holder_keeps_the_line_alive_past_the_stall_window(line) -> None:
    """2026-11-25: 3h10m of suite, and the waiter still has its place at the end of it."""
    ends = 190 * 60.0
    line.hold("slow suite", _busy, ends=ends)

    assert line.wait().granted
    assert line.now >= ends > 3 * STALL


def test_a_holder_that_only_reports_progress_is_working(line) -> None:
    line.hold("waits on the network", _reporting(line), ends=2 * STALL)

    assert line.wait().granted


def test_a_hung_holder_stalls_the_line_after_the_full_window(line) -> None:
    """Idle 22 minutes before the waiter joined; the waiter's hour is still its own."""
    line.hold("deadlocked suite", _hung(line))

    message = line.wait_until_stalled()

    line.assert_stalled_at(STALL)
    assert "'deadlocked suite' has held 4 cpus" in message
    assert "may be hung" in message


def test_the_clock_runs_from_when_the_holder_was_last_seen_working(line) -> None:
    """Not from when it began to look hung, fifteen minutes on."""
    line.hold("suite", _busy_until(line, 1800))

    line.wait_until_stalled()

    line.assert_stalled_at(1800 + STALL)


def test_a_holder_nothing_measured_stalls_the_line_as_before(line) -> None:
    line.hold("older wrapper", _unmeasured)

    message = line.wait_until_stalled()

    line.assert_stalled_at(STALL)
    assert "whether it is working is not measured" in message


def test_a_docker_holder_is_unmeasured_whatever_its_stamps_say(line) -> None:
    """Its work is in the daemon's containers, so the wrapper's own CPU is not evidence."""
    line.hold("compose stack", _busy, pool=CapacityPool.DOCKER)

    message = line.wait_until_stalled()

    line.assert_stalled_at(STALL)
    assert "not measured" in message


def test_one_busy_holder_is_enough_and_the_hung_one_beside_it_is_named(line) -> None:
    ends = 2.5 * STALL
    line.hold("deadlocked suite", _hung(line), cpus=2.0)
    line.hold("working suite", _busy, cpus=2.0, ends=ends)

    message = line.wait_until_stalled()

    # Past an hour while the other worked, then the hung one's own hour.
    line.assert_stalled_at(ends + STALL)
    assert "'deadlocked suite'" in message
    first_holder = next(said for said in line.said if "ahead: '" in said)
    assert "deadlocked suite" in first_holder
    assert "may be hung" in first_holder


@pytest.mark.parametrize("stopped_at", [-3600.0, 1800.0])
def test_a_sampler_that_stopped_is_not_a_reading(line, stopped_at) -> None:
    """Its last word was "working"; an hour later that is no longer known."""
    lease = line.hold("suite", _sampled_until(line, stopped_at))
    if stopped_at < 0:
        _busy(lease, line.began + timedelta(seconds=stopped_at))
        line.session.add(lease)
        line.session.commit()

    message = line.wait_until_stalled()

    # A sample stands for three minutes before it is stale.
    last_word = max(0.0, stopped_at)
    assert last_word + STALL <= line.now < last_word + STALL + 180 + POLL_SLACK
    assert "not measured" in message


def test_a_holder_that_spins_forever_counts_for_four_hours_of_its_hold(line) -> None:
    line.hold("infinite loop", _busy)

    message = line.wait_until_stalled()

    line.assert_stalled_at(WORKING_HOLD_COUNTS_SECONDS + STALL)
    assert "still working, but past the 4h00m its activity counts for" in message


def test_a_stack_that_has_been_up_for_days_does_not_hide_a_hung_holder(line) -> None:
    stack = line.hold("dev stack", _busy, cpus=1.0)
    stack.granted_at = line.began - timedelta(days=2)
    line.hold("deadlocked suite", _hung(line), cpus=3.0)

    message = line.wait_until_stalled()

    line.assert_stalled_at(STALL)
    assert "'deadlocked suite'" in message


def test_a_zero_window_still_waits_only_for_an_immediate_grant(line) -> None:
    line.hold("busy", _busy)

    with pytest.raises(CapacityQueueStalled):
        line.wait(stall_seconds=0)

    assert line.now == 0
