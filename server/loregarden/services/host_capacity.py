"""What this machine's own capacity is — the host pool's ceiling.

The docker pool measures the Docker VM through `docker info`; this measures the
machine the VM, the test suites and the agents all run on. No daemon is
involved, so a host claim keeps working while Docker is stopped.

The derivation mirrors `docker_capacity.resolve_ceiling` — override, measured,
stale, unknown — and fails closed the same way: a machine nobody could measure
is not a machine with no limit.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timezone

from loregarden.config import settings
from loregarden.models.domain.enums import DockerCeilingSource
from loregarden.services.docker_capacity import Ceiling

logger = logging.getLogger(__name__)

_BYTES_PER_MB = 1024 * 1024

#: Runs `sysctl` on macOS, where `os.sysconf` has no physical-memory name.
#: Injected so tests hand in canned output instead of patching `subprocess`.
SysctlInvoke = Callable[[Sequence[str]], subprocess.CompletedProcess]


def _run_sysctl(argv: Sequence[str]) -> subprocess.CompletedProcess:
    return subprocess.run(  # noqa: S603 — fixed argv, no shell
        ["sysctl", *argv], capture_output=True, text=True, timeout=5, check=False
    )


@dataclass(frozen=True)
class HostProbe:
    """The machine's size, or why it could not be read. Never `(0, 0)` for "unknown"."""

    ncpu: int = 0
    mem_total_bytes: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return not self.error


def _memory_bytes(invoke: SysctlInvoke) -> int:
    """Physical memory in bytes. Raises with the reason when it cannot be read."""
    if sys.platform == "darwin":
        result = invoke(["-n", "hw.memsize"])
        if result.returncode != 0:
            raise OSError(f"sysctl hw.memsize exited {result.returncode}: {result.stderr.strip()}")
        return int(result.stdout.strip())
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


def probe_host(*, invoke: SysctlInvoke = _run_sysctl) -> HostProbe:
    ncpu = os.cpu_count() or 0
    if ncpu <= 0:
        return HostProbe(error="os.cpu_count() could not determine the cpu count")
    try:
        memory = _memory_bytes(invoke)
    except (OSError, ValueError, subprocess.TimeoutExpired) as exc:
        return HostProbe(error=f"could not read physical memory: {exc}")
    if memory <= 0:
        return HostProbe(error=f"physical memory read as {memory} bytes")
    return HostProbe(ncpu=ncpu, mem_total_bytes=memory)


def resolve_host_ceiling(
    *,
    invoke: SysctlInvoke = _run_sysctl,
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

    probe = probe_host(invoke=invoke)
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
