"""Whether the command held under a lease is still doing anything.

The lease is renewed by the process *holding* it, so it says nothing about the
command: one that deadlocks keeps its grant for as long as its wrapper lives.
Its progress file says little more. A command that writes none is silent by
design, and on 2026-11-25 a test inside a suite wrote "7/7 (100%)" over the
suite's own file, so a run at 77% was drawn as silent and possibly hung.

CPU time is evidence no reporter has to volunteer. `CpuSync` samples it over the
command's whole process tree and records on the lease when the tree last used
any; `lease_activity` reads that back together with the last progress report.

**This never ends a lease.** A command waiting on a network, or on the docker
daemon, also uses no CPU, so a person decides about one that looks hung. What it
does decide is whether the line behind a lease is alive (`seconds_since_working`,
read by `capacity_run`).

**Not measured is not idle.** A lease with no recent sample — held by something
other than `capacity run`, by a build older than this, or across a laptop's
sleep — has no activity reading at all, rather than one that says "hung".
"""

from __future__ import annotations

import os
import subprocess
import threading
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass
from datetime import datetime, timedelta

from loregarden.models.domain import CapacityPool, DockerLease
from loregarden.models.domain.enums import utcnow
from loregarden.services.capacity_progress import Report, SessionFactory, format_seconds
from loregarden.services.docker_ledger import OCCUPYING, as_utc
from sqlalchemy.exc import SQLAlchemyError
from sqlmodel import Session

#: How often the tree is sampled. One `ps` a minute; 0.3–2.3s each on a machine
#: at load 90.
SAMPLE_INTERVAL_SECONDS = 60.0

#: The share of one cpu a tree must use across a sample to count as working.
#: Measured 2026-11-25 at load ~90, where everything runs slowest: a deadlocked
#: `pytest -n 3` used 0.00–0.03%, a capacity wrapper polling in line 0.9–1.6%,
#: and a live `pytest -n 3` 72–111%.
WORKING_CPU_SHARE = 0.05

#: No CPU and no progress for this long, and the command looks hung. Well past
#: the suite's 120s per-test timeout, which ends an ordinary hang by itself.
HUNG_AFTER_SECONDS = 15 * 60

#: How long into a hold its activity keeps the line behind it alive. A command
#: spinning in a loop uses CPU forever, so past this a hold has to end to count.
#: Above the longest test run the ledger has held (181 minutes, a server suite at
#: load ~60 on 2026-11-25), and it also leaves out a dev stack, which is busy for
#: days (27h and 68h on the ledger) and was never about to finish.
WORKING_HOLD_COUNTS_SECONDS = 4 * 3600

#: A gap this many samples long was not watched, so it says nothing.
_MISSED_SAMPLES = 3

_PS_TIMEOUT_SECONDS = 15


@dataclass(frozen=True)
class LeaseActivity:
    """How long since the held command last used CPU or reported progress."""

    idle_seconds: int
    looks_hung: bool

    def as_dict(self) -> dict:
        return {"idle_seconds": self.idle_seconds, "looks_hung": self.looks_hung}


def lease_activity(lease: DockerLease, *, now: datetime) -> LeaseActivity | None:
    """What the samples say, or None when nothing recent measured this lease.

    None for a docker claim too: its work runs in the daemon's containers, which
    are no descendants of the command, so a busy stack would read as idle.
    """
    if lease.pool is CapacityPool.DOCKER or lease.status not in OCCUPYING:
        return None
    sampled_at, active_at = as_utc(lease.cpu_sampled_at), as_utc(lease.cpu_active_at)
    if sampled_at is None or active_at is None:
        return None
    if now - sampled_at > timedelta(seconds=SAMPLE_INTERVAL_SECONDS * _MISSED_SAMPLES):
        return None
    reported_at = as_utc(lease.progress_at)
    last = max(active_at, reported_at) if reported_at else active_at
    # To the last sample, not to now: a sampler that is late (a busy machine,
    # where `ps` takes seconds) has not seen the command idle since.
    idle = max(0, int((sampled_at - last).total_seconds()))
    return LeaseActivity(idle_seconds=idle, looks_hung=idle >= HUNG_AFTER_SECONDS)


def describe_hang(lease: DockerLease, *, now: datetime) -> str:
    """ "no CPU use or progress for 22m05s, may be hung", or "" for one that is working."""
    activity = lease_activity(lease, now=now)
    if activity is None or not activity.looks_hung:
        return ""
    return f"no CPU use or progress for {format_seconds(activity.idle_seconds)}, may be hung"


def past_counting(lease: DockerLease, *, now: datetime) -> bool:
    """Whether this hold is so old that its activity no longer says the line is alive."""
    granted_at = as_utc(lease.granted_at)
    return (
        granted_at is not None and (now - granted_at).total_seconds() >= WORKING_HOLD_COUNTS_SECONDS
    )


def seconds_since_working(leases: Iterable[DockerLease], *, now: datetime) -> int | None:
    """How long since any of these holders was last seen working, or None.

    None when no reading says: unmeasured, or measured on a hold old enough that
    its activity no longer counts. Neither is idle, and neither is working.
    """
    readings = (
        lease_activity(lease, now=now) for lease in leases if not past_counting(lease, now=now)
    )
    return min((reading.idle_seconds for reading in readings if reading is not None), default=None)


def parse_cpu_time(text: str) -> float:
    """Seconds in `ps`'s cpu time: "1:23.45" on macOS, "[2-]03:04:05" on Linux."""
    days, _, clock = text.rpartition("-")
    seconds = 0.0
    for part in clock.split(":"):
        seconds = seconds * 60 + float(part)
    return seconds + (int(days) * 86400 if days else 0)


def parse_process_table(text: str) -> dict[int, tuple[int, float]]:
    """pid -> (parent pid, cpu seconds), from `ps -A -o pid=,ppid=,time=`."""
    table: dict[int, tuple[int, float]] = {}
    for line in text.splitlines():
        if not line.strip():
            continue
        pid, parent, cpu = line.split()
        table[int(pid)] = (int(parent), parse_cpu_time(cpu))
    return table


def tree_cpu_seconds(table: dict[int, tuple[int, float]], root: int) -> dict[int, float]:
    """CPU seconds of every descendant of `root`, not counting `root` itself."""
    children: dict[int, list[int]] = {}
    for pid, (parent, _cpu) in table.items():
        children.setdefault(parent, []).append(pid)
    found: dict[int, float] = {}
    pending = list(children.get(root, ()))
    while pending:
        pid = pending.pop()
        if pid in found:
            continue
        found[pid] = table[pid][1]
        pending.extend(children.get(pid, ()))
    return found


def read_tree_cpu(root: int) -> dict[int, float]:
    """This moment's CPU seconds per descendant of `root`. Raises when `ps` cannot say."""
    result = subprocess.run(
        ["ps", "-A", "-o", "pid=,ppid=,time="],
        capture_output=True,
        text=True,
        timeout=_PS_TIMEOUT_SECONDS,
        check=True,
    )
    return tree_cpu_seconds(parse_process_table(result.stdout), root)


def record_cpu_sample(session: Session, lease_id: str, *, active: bool) -> bool:
    """Stamp a sample on the lease. False when the lease is gone."""
    lease = session.get(DockerLease, lease_id)
    if lease is None:
        return False
    now = utcnow()
    lease.cpu_sampled_at = now
    if active or lease.cpu_active_at is None:
        lease.cpu_active_at = now
    session.add(lease)
    session.commit()
    return True


class CpuSync:
    """Samples the held command's CPU onto its lease from a thread, while it runs.

    Counts per process rather than over the tree's total: a worker that exits
    takes its CPU time out of the total, which would hide the work of the rest.
    Work done by a process that starts and ends between two samples is missed,
    which costs a reading only for a command that does nothing else.
    """

    def __init__(
        self,
        session_factory: SessionFactory,
        lease_id: str,
        report: Report,
        *,
        root: int | None = None,
        interval: float = SAMPLE_INTERVAL_SECONDS,
        read: Callable[[int], dict[int, float]] = read_tree_cpu,
        clock: Callable[[], float] = time.time,
    ) -> None:
        self._session_factory = session_factory
        self._lease_id = lease_id
        self._report = report
        self._root = os.getpid() if root is None else root
        self._interval = interval
        self._read = read
        # Wall clock: it keeps running while a laptop sleeps, so a gap shows.
        self._clock = clock
        self._last: dict[int, float] | None = None
        self._last_at = 0.0
        self._failing = False
        self._stop = threading.Event()
        self._thread = threading.Thread(target=self._run, name="capacity-cpu", daemon=True)

    def __enter__(self) -> CpuSync:
        self._thread.start()
        return self

    def __exit__(self, *_exc: object) -> None:
        self._stop.set()
        self._thread.join(timeout=5)

    def _run(self) -> None:
        # No sample at the start: most held commands end inside one interval,
        # and none of those needs a `ps`.
        while not self._stop.wait(self._interval):
            self.sample()

    def sample(self) -> None:
        """Take one sample and record it. A failure is said once, until one succeeds."""
        try:
            cpu = self._read(self._root)
        except (OSError, subprocess.SubprocessError, ValueError) as exc:
            self._say(f"capacity: could not sample the command's CPU: {exc}")
            return
        now = self._clock()
        active = self._is_working(cpu, now)
        self._last, self._last_at = cpu, now
        try:
            with self._session_factory() as session:
                record_cpu_sample(session, self._lease_id, active=active)
        except SQLAlchemyError as exc:
            # A locked database, usually; the next sample writes again.
            self._say(f"capacity: could not record CPU use on {self._lease_id}: {exc}")
            return
        self._failing = False

    def _is_working(self, cpu: dict[int, float], now: float) -> bool:
        if self._last is None:
            return True
        elapsed = now - self._last_at
        if elapsed <= 0 or elapsed > self._interval * _MISSED_SAMPLES:
            return True  # nobody was watching: start the idle clock again
        used = sum(max(0.0, spent - self._last.get(pid, 0.0)) for pid, spent in cpu.items())
        return used / elapsed >= WORKING_CPU_SHARE

    def _say(self, message: str) -> None:
        if not self._failing:
            self._report(message)
        self._failing = True
