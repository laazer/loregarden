"""Ceiling derivation, and the four things a failed probe must not become.

The contract this file pins, beyond arithmetic:

- A `docker info` that fails is neither "no capacity" nor "unlimited capacity".
  Three of the six tests here exist because those are the two ways this feature
  could silently do the opposite of its job.
- A stale ceiling keeps working *and* says it is stale. The alternative — refuse
  everything the moment Docker Desktop restarts — makes the ledger the outage.
- Weights are resolved at claim time, so this table can be retuned later without
  rewriting what live leases are accounted at.
"""

from __future__ import annotations

import json
import subprocess
from datetime import datetime, timedelta, timezone
from unittest import mock

import pytest
from loregarden.models.domain.enums import (
    DockerCeilingSource,
    DockerFootprint,
    DockerProbeOutcome,
)
from loregarden.services.docker_capacity import (
    Ceiling,
    ClaimTooVague,
    clear_probe_cache,
    probe_docker_info,
    resolve_ceiling,
    resolve_weights,
)

NOW = datetime(2026, 9, 9, 12, 0, tzinfo=timezone.utc)
#: This machine, as measured: 8 cpus, ~15.6 GiB.
HOST_INFO = {"NCPU": 8, "MemTotal": 16763441152}


def _completed(*, returncode: int = 0, stdout: str = "", stderr: str = ""):
    return subprocess.CompletedProcess(args=[], returncode=returncode, stdout=stdout, stderr=stderr)


def _ok_invoke(payload: dict | None = None):
    body = json.dumps(HOST_INFO if payload is None else payload)
    return lambda args: _completed(stdout=body)


@pytest.fixture(autouse=True)
def _clean_cache():
    clear_probe_cache()
    yield
    clear_probe_cache()


# ---- weights -----------------------------------------------------------


def test_named_sizes_resolve_to_weights() -> None:
    assert resolve_weights(DockerFootprint.STACK) == (2.0, 4096)
    assert resolve_weights(DockerFootprint.LIGHT) == (0.5, 512)


def test_explicit_weights_beat_the_named_size() -> None:
    assert resolve_weights(DockerFootprint.LIGHT, cpus=3.0, memory_mb=6000) == (3.0, 6000)


@pytest.mark.parametrize(
    ("footprint", "cpus", "memory_mb"),
    [
        (DockerFootprint.NONE, 0.0, 0),
        (DockerFootprint.CUSTOM, 0.0, 0),
        (DockerFootprint.CUSTOM, 2.0, 0),
        (DockerFootprint.CUSTOM, 0.0, 4096),
    ],
)
def test_a_claim_with_no_usable_price_is_refused(
    footprint: DockerFootprint, cpus: float, memory_mb: int
) -> None:
    """Half a price is not a price, and a silent default under-books exactly the
    claims whose caller did not think about size."""
    with pytest.raises(ClaimTooVague):
        resolve_weights(footprint, cpus=cpus, memory_mb=memory_mb)


# ---- probing -----------------------------------------------------------


def test_probe_reads_ncpu_and_memtotal() -> None:
    probe = probe_docker_info(invoke=_ok_invoke())
    assert probe.outcome is DockerProbeOutcome.OK
    assert probe.ncpu == 8
    assert probe.mem_total_bytes == 16763441152


@pytest.mark.parametrize(
    ("invoke", "expected"),
    [
        (
            lambda args: _completed(returncode=1, stderr="Cannot connect to the Docker daemon"),
            DockerProbeOutcome.DAEMON_UNREACHABLE,
        ),
        (lambda args: _completed(stdout="not json"), DockerProbeOutcome.MALFORMED_OUTPUT),
        (
            lambda args: _completed(stdout=json.dumps({"NCPU": 0, "MemTotal": 0})),
            DockerProbeOutcome.MALFORMED_OUTPUT,
        ),
    ],
)
def test_probe_failures_are_distinguished(invoke, expected: DockerProbeOutcome) -> None:
    probe = probe_docker_info(invoke=invoke)
    assert probe.outcome is expected
    assert probe.error
    assert not probe.ok


def test_an_unreachable_daemon_is_read_from_server_errors_not_the_exit_code() -> None:
    """`docker info` exits ZERO when the daemon is unreachable.

    Measured on a real host: `DOCKER_HOST=tcp://127.0.0.1:1 docker info
    --format '{{json .}}'` returns exit 0 with client-side data only and the
    failure in `ServerErrors`. Reading the exit code alone diagnoses that as
    MALFORMED_OUTPUT, which sends whoever reads the doctor finding hunting a
    docker version incompatibility when the answer is "start Docker".
    """
    body = json.dumps(
        {
            "ServerErrors": ["Cannot connect to the Docker daemon at tcp://127.0.0.1:1."],
            "ClientInfo": {"Debug": False},
        }
    )
    probe = probe_docker_info(invoke=lambda args: _completed(returncode=0, stdout=body))
    assert probe.outcome is DockerProbeOutcome.DAEMON_UNREACHABLE
    assert "Cannot connect" in probe.error


def test_a_payload_with_neither_an_error_nor_the_fields_is_malformed() -> None:
    """Still distinguished from the case above: no error reported and nothing to
    read is a docker this parser does not understand, and that has a different
    remedy from a stopped daemon."""
    probe = probe_docker_info(
        invoke=lambda args: _completed(stdout=json.dumps({"ClientInfo": {"Debug": False}}))
    )
    assert probe.outcome is DockerProbeOutcome.MALFORMED_OUTPUT


def test_missing_binary_and_timeout_do_not_escape_as_exceptions() -> None:
    def missing(args):
        raise FileNotFoundError("docker")

    def slow(args):
        raise subprocess.TimeoutExpired(cmd="docker info", timeout=10)

    assert probe_docker_info(invoke=missing).outcome is DockerProbeOutcome.BINARY_MISSING
    assert probe_docker_info(invoke=slow).outcome is DockerProbeOutcome.TIMED_OUT


# ---- ceiling -----------------------------------------------------------


def test_ceiling_is_headroom_of_the_machine_less_the_reserved_baseline() -> None:
    ceiling = resolve_ceiling(invoke=_ok_invoke(), now=NOW, use_cache=False)
    assert ceiling.source is DockerCeilingSource.PROBE
    # 8 * 0.75 - 1.0 reserved
    assert ceiling.cpus == pytest.approx(5.0)
    # 15987 MiB * 0.75 - 2048 reserved
    assert ceiling.memory_mb == pytest.approx(9942, abs=2)
    assert ceiling.leases == 4
    assert ceiling.known


def test_config_override_wins_without_probing() -> None:
    invoke = mock.Mock()
    with mock.patch.multiple(
        "loregarden.services.docker_capacity.settings",
        docker_capacity_cpus=3.0,
        docker_capacity_memory_mb=6000,
    ):
        ceiling = resolve_ceiling(invoke=invoke, now=NOW, use_cache=False)
    assert ceiling.source is DockerCeilingSource.CONFIG_OVERRIDE
    assert (ceiling.cpus, ceiling.memory_mb) == (3.0, 6000)
    invoke.assert_not_called()


def test_a_failed_probe_keeps_the_last_measurement_and_says_it_is_stale() -> None:
    """The ledger must not become the outage when Docker Desktop restarts."""
    previous = resolve_ceiling(invoke=_ok_invoke(), now=NOW, use_cache=False)
    ceiling = resolve_ceiling(
        invoke=lambda args: _completed(returncode=1, stderr="daemon down"),
        now=NOW + timedelta(minutes=5),
        previous=previous,
        use_cache=False,
    )
    assert ceiling.source is DockerCeilingSource.STALE_PROBE
    assert ceiling.cpus == previous.cpus
    assert ceiling.memory_mb == previous.memory_mb
    assert "daemon down" in ceiling.error
    assert ceiling.known


def test_a_failed_probe_with_nothing_ever_measured_is_unknown_not_unlimited() -> None:
    """Fail closed. An unmeasured machine must not read as an idle one, and a
    zero ceiling must be distinguishable from a full one — `known` is what
    admission checks before it refuses."""
    ceiling = resolve_ceiling(
        invoke=lambda args: _completed(returncode=1, stderr="no daemon"),
        now=NOW,
        previous=None,
        use_cache=False,
    )
    assert ceiling.source is DockerCeilingSource.UNKNOWN
    assert not ceiling.known
    assert (ceiling.cpus, ceiling.memory_mb, ceiling.leases) == (0.0, 0, 0)
    assert "no daemon" in ceiling.error


def test_an_unknown_previous_ceiling_does_not_get_promoted_to_stale() -> None:
    unknown = Ceiling(0.0, 0, 0, DockerCeilingSource.UNKNOWN)
    ceiling = resolve_ceiling(
        invoke=lambda args: _completed(returncode=1, stderr="still down"),
        now=NOW,
        previous=unknown,
        use_cache=False,
    )
    assert ceiling.source is DockerCeilingSource.UNKNOWN


def test_the_probe_is_cached_so_polling_does_not_spawn_a_process_per_call() -> None:
    calls: list[list[str]] = []

    def counting(args):
        calls.append(list(args))
        return _completed(stdout=json.dumps(HOST_INFO))

    for offset in range(5):
        resolve_ceiling(invoke=counting, now=NOW + timedelta(seconds=offset))
    assert len(calls) == 1

    resolve_ceiling(invoke=counting, now=NOW + timedelta(seconds=3600))
    assert len(calls) == 2
