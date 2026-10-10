"""`services/host_capacity`: the host pool's ceiling, read without spawning a process.

The probe used to fork `sysctl -n hw.memsize` with a 5s timeout, and under memory
pressure the fork alone outran it — refusing host claims on a fresh pool at the
moment the machine was busiest. The properties:

- physical memory is read in-process, and agrees with `sysctl`;
- a read that fails says why, and never reports a size it did not read;
- a failed probe falls back to the last measured ceiling, or to unknown.
"""

from __future__ import annotations

import subprocess
import sys
from datetime import datetime, timezone
from unittest import mock

import pytest
from loregarden.config import settings
from loregarden.models.domain.enums import DockerCeilingSource
from loregarden.services import host_capacity
from loregarden.services.docker_capacity import Ceiling


@pytest.fixture(name="probed")
def probed_fixture(monkeypatch) -> None:
    """Turn off conftest's config override, so the ceiling comes from the probe."""
    monkeypatch.setattr(settings, "host_capacity_cpus", 0.0)
    monkeypatch.setattr(settings, "host_capacity_memory_mb", 0)


def _fails() -> int:
    raise OSError(5, "sysctlbyname(hw.memsize) failed: Input/output error")


def test_physical_memory_is_read_without_spawning_a_process() -> None:
    with mock.patch.object(subprocess, "Popen", side_effect=AssertionError("spawned")):
        probe = host_capacity.probe_host()

    assert probe.ok, probe.error
    assert probe.mem_total_bytes > 0


@pytest.mark.skipif(sys.platform != "darwin", reason="hw.memsize is a macOS sysctl")
def test_the_in_process_read_agrees_with_sysctl() -> None:
    reported = subprocess.run(
        ["sysctl", "-n", "hw.memsize"], capture_output=True, text=True, check=True, timeout=60
    )

    assert host_capacity.probe_host().mem_total_bytes == int(reported.stdout)


@pytest.mark.parametrize(
    ("reader", "cause"),
    [(_fails, "Input/output error"), (lambda: 0, "read as 0 bytes")],
)
def test_a_failed_read_says_why_and_reports_no_size(reader, cause) -> None:
    probe = host_capacity.probe_host(read_memory=reader)

    assert not probe.ok
    assert cause in probe.error
    assert probe.mem_total_bytes == 0


def test_a_failed_probe_keeps_the_last_measured_ceiling(probed) -> None:
    measured = datetime(2026, 10, 3, tzinfo=timezone.utc)
    previous = Ceiling(
        cpus=7.0, memory_mb=48332, leases=6, source=DockerCeilingSource.PROBE, probed_at=measured
    )

    ceiling = host_capacity.resolve_host_ceiling(read_memory=_fails, previous=previous)

    assert ceiling.source is DockerCeilingSource.STALE_PROBE
    assert (ceiling.cpus, ceiling.memory_mb, ceiling.probed_at) == (7.0, 48332, measured)


def test_a_failed_first_probe_is_unknown_not_unlimited(probed) -> None:
    ceiling = host_capacity.resolve_host_ceiling(read_memory=_fails)

    assert ceiling.source is DockerCeilingSource.UNKNOWN
    assert not ceiling.known
