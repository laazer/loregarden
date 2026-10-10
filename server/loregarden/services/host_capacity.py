"""What this machine's own capacity is — the host pool's ceiling.

The docker pool measures the Docker VM through `docker info`; this measures the
machine the VM, the test suites and the agents all run on. No daemon is
involved, so a host claim keeps working while Docker is stopped.

The derivation mirrors `docker_capacity.resolve_ceiling` — override, measured,
stale, unknown — and fails closed the same way: a machine nobody could measure
is not a machine with no limit.
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
from loregarden.models.domain.enums import DockerCeilingSource
from loregarden.services.docker_capacity import Ceiling

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024

#: Reads physical memory in bytes, raising OSError with the reason when it cannot.
#: Injected so tests hand in a canned size or failure.
MemoryReader = Callable[[], int]


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
