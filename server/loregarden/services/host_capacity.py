"""What this machine's own capacity is — the host pool's ceiling.

The docker pool measures the Docker VM through `docker info`; this measures the
machine the VM, the test suites and the agents all run on. No daemon is
involved, so a host claim keeps working while Docker is stopped.

The derivation mirrors `docker_capacity.resolve_ceiling` — override, measured,
stale, unknown — and fails closed the same way: a machine nobody could measure
is not a machine with no limit.

**The ceiling is the machine's size; it is not what is free.** It is static:
`cpu_count * headroom - reserved`, 7 cpus on the 10-core development machine.
The ledger only sees processes that reserve through it, and on 2026-11-09 the
window server, a browser, `fseventsd`, Bluetooth and the dev server's reloader
used about 7 cores between them while a suite held a "heavy" 4 of the 7 — load
average 41-72, 0% idle. `external_cpus` is that missing figure: busy cores
(the 1-minute load average, capped at the core count) less what the ledger
holds. The drain reads it through `docker_ledger.load_gate_cpus`.

It holds a claim back the way GNU make's `-l` does (`mirror/make` `src/job.c` at
`c63a5bc6`, `start_waiting_job`: a job waits on
`job_slots_used > 0 && load_too_high()`).
Two parts of make's rule carry over unchanged: the load counts only while
something is already running, so an empty ledger always admits its head and
the line cannot deadlock on load nobody in it can release; and make adds its
own recently started jobs to a load average that has not caught up with them,
which here is subtracting `held_cpus`. Two parts differ. Make fails open when
`getloadavg` fails (it warns and treats load as 0); this module fails closed,
counting the whole ceiling as busy. And make waits indefinitely, while a claim
here is held back for at most `host_load_patience_seconds` of its wait: an
agent's 0.25-cpu lease is always held, so make's "nothing running" escape would
rarely open, and a box that is always busy would otherwise starve the line.
Claims under `host_load_gate_min_cpus` (agent runs, light services) are never
held back: their wait budget is shorter than a busy afternoon.
"""

from __future__ import annotations

import ctypes
import ctypes.util
import logging
import os
import sys
from collections.abc import Callable
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.config import settings
from loregarden.models.domain.enums_capacity import DockerCeilingSource
from loregarden.services.docker_capacity import Ceiling

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024

#: Reads physical memory in bytes, raising OSError with the reason when it cannot.
#: Injected so tests hand in a canned size or failure.
MemoryReader = Callable[[], int]

#: Reads the 1-minute load average, raising OSError when it cannot. Injected for tests.
LoadReader = Callable[[], float]


def _read_load_average() -> float:
    """The 1-minute load average, read in-process (`getloadavg(3)`), as make reads it."""
    return os.getloadavg()[0]


@dataclass(frozen=True)
class ExternalLoad:
    """Busy cores the ledger does not hold, or why they could not be measured.

    A failed read reports the whole ceiling as busy rather than zero: "nobody
    could measure the load" must not read as "the machine is idle".
    """

    cpus: float
    error: str = ""


def external_cpus(
    *,
    ncpu: int,
    ceiling_cpus: float,
    held_cpus: float,
    read_load: LoadReader = _read_load_average,
) -> ExternalLoad:
    """Busy cores less what the ledger holds; see the module docstring.

    The load average is capped at the core count: a load of 60 on 10 cores says
    the machine is full, not that 50 more cores are busy. The ledger's own
    holders are assumed to use what they hold, so their part is not counted
    twice; a holder using more than it booked is counted as outside load, which
    is the truth about the machine either way.
    """
    try:
        load = read_load()
    except OSError as exc:
        return ExternalLoad(cpus=ceiling_cpus, error=f"could not read the load average: {exc}")
    if load < 0:
        return ExternalLoad(cpus=ceiling_cpus, error=f"load average read as {load}")
    return ExternalLoad(cpus=round(max(0.0, min(float(ncpu), load) - held_cpus), 2))


def _read_physical_memory() -> int:
    """Physical memory in bytes, read in-process.

    macOS has no `os.sysconf` name for it, so this asks libc's `sysctlbyname`
    directly. It used to fork `sysctl -n hw.memsize` with a 5s timeout, and on a
    box under memory pressure — exactly when capacity matters — spawning the
    process alone outran the timeout (2026-10-03, load average ~28). The value
    is constant for the machine's uptime; reading it should not need a process.
    """
    if sys.platform != "darwin":
        return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")
    libc = ctypes.CDLL(ctypes.util.find_library("c"), use_errno=True)
    value = ctypes.c_uint64(0)
    size = ctypes.c_size_t(ctypes.sizeof(value))
    if libc.sysctlbyname(b"hw.memsize", ctypes.byref(value), ctypes.byref(size), None, 0) != 0:
        errno = ctypes.get_errno()
        raise OSError(errno, f"sysctlbyname(hw.memsize) failed: {os.strerror(errno)}")
    return value.value


@dataclass(frozen=True)
class HostProbe:
    """The machine's size, or why it could not be read. Never `(0, 0)` for "unknown"."""

    ncpu: int = 0
    mem_total_bytes: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def probe_host(*, read_memory: MemoryReader = _read_physical_memory) -> HostProbe:
    ncpu = os.cpu_count() or 0
    if ncpu <= 0:
        return HostProbe(error="os.cpu_count() could not determine the cpu count")
    try:
        memory = read_memory()
    except (OSError, ValueError) as exc:
        return HostProbe(error=f"could not read physical memory: {exc}")
    if memory <= 0:
        return HostProbe(error=f"physical memory read as {memory} bytes")
    return HostProbe(ncpu=ncpu, mem_total_bytes=memory)


def resolve_host_ceiling(
    *,
    read_memory: MemoryReader = _read_physical_memory,
    now: datetime | None = None,
    previous: Ceiling | None = None,
) -> Ceiling:
    """The host ceiling to enforce, and where it came from. See the module docstring."""
    stamp = now or datetime.now(timezone.utc)
    if settings.host_capacity_cpus > 0 and settings.host_capacity_memory_mb > 0:
        return Ceiling(
            cpus=settings.host_capacity_cpus,
            memory_mb=settings.host_capacity_memory_mb,
            leases=settings.host_capacity_max_leases,
            source=DockerCeilingSource.CONFIG_OVERRIDE,
            probed_at=stamp,
        )

    probe = probe_host(read_memory=read_memory)
    if probe.ok:
        headroom = settings.host_capacity_headroom
        return Ceiling(
            cpus=max(0.0, round(probe.ncpu * headroom - settings.host_reserved_cpus, 2)),
            memory_mb=max(
                0,
                int(probe.mem_total_bytes / _BYTES_PER_MB * headroom)
                - settings.host_reserved_memory_mb,
            ),
            leases=settings.host_capacity_max_leases,
            source=DockerCeilingSource.PROBE,
            probed_at=stamp,
        )

    logger.warning("Host capacity probe failed: %s", probe.error)
    if previous is not None and previous.known:
        return Ceiling(
            cpus=previous.cpus,
            memory_mb=previous.memory_mb,
            leases=previous.leases,
            source=DockerCeilingSource.STALE_PROBE,
            probed_at=previous.probed_at,
            error=probe.error,
        )
    return Ceiling(
        cpus=0.0, memory_mb=0, leases=0, source=DockerCeilingSource.UNKNOWN, error=probe.error
    )


def host_external_load(
    ceiling: Ceiling, *, held_cpus: float, read_load: LoadReader = _read_load_average
) -> ExternalLoad:
    """The outside load to record beside `ceiling`; zero where it is not measured.

    Not measured when the gate is switched off, when an operator set the ceiling
    by hand (an override is a budget its owner chose, load included), or when
    the ceiling is unknown — which refuses every claim on its own.
    """
    if not settings.host_load_aware or not ceiling.known:
        return ExternalLoad(cpus=0.0)
    if ceiling.source is DockerCeilingSource.CONFIG_OVERRIDE:
        return ExternalLoad(cpus=0.0)
    ncpu = os.cpu_count() or 0
    if ncpu <= 0:
        return ExternalLoad(
            cpus=ceiling.cpus, error="os.cpu_count() could not determine the cpu count"
        )
    load = external_cpus(
        ncpu=ncpu, ceiling_cpus=ceiling.cpus, held_cpus=held_cpus, read_load=read_load
    )
    if load.error:
        logger.warning("Host load probe failed, counting the host as busy: %s", load.error)
    return load
