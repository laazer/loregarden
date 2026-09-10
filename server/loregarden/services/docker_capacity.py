"""What this machine's docker capacity is, and what a claim on it costs.

Two jobs, both of them measurement rather than bookkeeping — the ledger itself
lives in `docker_leases`.

**Pricing.** A claim says how big it is as a named size, and `CLASS_WEIGHTS`
turns that into cpus and memory. The resolved numbers are what gets stored on
the lease, not the class, so editing this table later cannot retroactively
change what live leases are accounted at.

**The ceiling.** Derived from `docker info`, and the derivation has four
outcomes rather than a number and a shrug. A probe that fails is not a machine
with no capacity, and it is not a machine with infinite capacity either — it is
a machine nobody has measured, and admission refuses on it. The one case that
keeps working is a probe that used to succeed: the previous ceiling stays in
force, marked `STALE_PROBE`, and every payload that quotes it says so.

The headroom fraction and the reserved baseline are separate knobs on purpose.
Headroom is "how much of this machine am I willing to book"; the reserve is "how
much of it is already spoken for by containers no lease will ever account for" —
a personal Postgres, a Redis somebody left up. The ledger tracks leases only and
must not pretend to attribute load it did not grant.
"""

from __future__ import annotations

import logging
import subprocess
import threading
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone

from loregarden.config import settings
from loregarden.models.domain.enums import (
    DockerCeilingSource,
    DockerFootprint,
    DockerProbeOutcome,
)
from loregarden.services.docker_subprocess import DockerVerbRefused, run_docker
from pydantic import BaseModel, ConfigDict, Field, TypeAdapter, ValidationError

logger = logging.getLogger(__name__)

#: The invocation seam. Injected keyword-only at every call site so tests hand in
#: canned `CompletedProcess` objects instead of patching a module global — this
#: module binds `run_docker` at import, so patching the source module would leave
#: the service calling the original and the test would pass having exercised
#: nothing.
DockerInvoke = Callable[[Sequence[str]], subprocess.CompletedProcess]

#: Resolved cost of each named size, as (cpus, memory_mb). Sized against a
#: developer machine rather than a server: `service` is one container with a
#: database in it, `stack` is a compose file for an app under test, `heavy` is a
#: browser-driving end-to-end suite.
CLASS_WEIGHTS: dict[DockerFootprint, tuple[float, int]] = {
    DockerFootprint.LIGHT: (0.5, 512),
    DockerFootprint.SERVICE: (1.0, 1024),
    DockerFootprint.STACK: (2.0, 4096),
    DockerFootprint.HEAVY: (4.0, 8192),
}

_BYTES_PER_MB = 1024 * 1024


class DockerInfo(BaseModel):
    """The fields of `docker info` this ledger reads.

    A model rather than dictionary lookups: the payload is a third-party
    document, `NCPU`/`MemTotal` are absent whenever the daemon is unreachable,
    and `ServerErrors` is the only place that failure is reported — `docker info`
    exits 0 either way.
    """

    model_config = ConfigDict(extra="ignore")

    NCPU: int | None = None
    MemTotal: int | None = None
    ServerErrors: list[str] = Field(default_factory=list)


_INFO_PAYLOAD = TypeAdapter(DockerInfo)


@dataclass(frozen=True)
class CapacityProbe:
    """What `docker info` said, or why it did not say anything.

    A failure-carrying result rather than a `(cpus, memory)` tuple that reads
    `(0, 0)` when the daemon is unreachable: a machine with no capacity and a
    machine that could not be asked must not share a value.
    """

    outcome: DockerProbeOutcome
    ncpu: int = 0
    mem_total_bytes: int = 0
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is DockerProbeOutcome.OK


@dataclass(frozen=True)
class Ceiling:
    """The limit in force, and how much it can be trusted."""

    cpus: float
    memory_mb: int
    leases: int
    source: DockerCeilingSource
    probed_at: datetime | None = None
    error: str = ""

    @property
    def known(self) -> bool:
        return self.source is not DockerCeilingSource.UNKNOWN

    def as_dict(self) -> dict:
        return {
            "cpus": self.cpus,
            "memory_mb": self.memory_mb,
            "leases": self.leases,
            "source": self.source.value,
            "probed_at": self.probed_at.isoformat() if self.probed_at else None,
            "error": self.error,
        }


class ClaimTooVague(ValueError):
    """A reservation named neither a footprint nor explicit weights.

    Refused rather than defaulted. A silent default under-books the machine on
    exactly the claims whose caller did not think about size, which are the ones
    most likely to be large.
    """


def resolve_weights(
    footprint: DockerFootprint, *, cpus: float = 0.0, memory_mb: int = 0
) -> tuple[float, int]:
    """The cpus and memory a claim actually costs.

    Explicit weights win over the named size, and supplying only one of the two
    is as much of an error as supplying neither — half a price is not a price.
    """
    if cpus > 0 and memory_mb > 0:
        return (cpus, memory_mb)
    if cpus > 0 or memory_mb > 0:
        raise ClaimTooVague(
            f"a custom claim needs both cpus and memory_mb; got cpus={cpus}, memory_mb={memory_mb}"
        )
    weights = CLASS_WEIGHTS.get(footprint)
    if weights is None:
        raise ClaimTooVague(
            f"footprint {footprint.value!r} carries no weights; "
            f"name one of {sorted(c.value for c in CLASS_WEIGHTS)} or pass cpus and memory_mb"
        )
    return weights


def probe_docker_info(*, invoke: DockerInvoke = run_docker) -> CapacityProbe:
    """Ask the daemon how big this machine is.

    Every failure mode gets its own outcome, because the remediations differ: a
    missing binary is an install, an unreachable daemon is a start, a timeout is
    a machine under load, and malformed output is a docker version this parser
    does not understand.
    """
    try:
        result = invoke(["info", "--format", "{{json .}}"])
    except FileNotFoundError as exc:
        return CapacityProbe(DockerProbeOutcome.BINARY_MISSING, error=str(exc))
    except subprocess.TimeoutExpired as exc:
        return CapacityProbe(DockerProbeOutcome.TIMED_OUT, error=str(exc))
    except DockerVerbRefused as exc:  # pragma: no cover — programming error, not a runtime state
        return CapacityProbe(DockerProbeOutcome.REFUSED_VERB, error=str(exc))

    if result.returncode != 0:
        return CapacityProbe(
            DockerProbeOutcome.DAEMON_UNREACHABLE,
            error=(result.stderr or "").strip() or f"docker info exited {result.returncode}",
        )

    try:
        payload = _INFO_PAYLOAD.validate_json(result.stdout or "")
    except ValidationError as exc:
        return CapacityProbe(
            DockerProbeOutcome.MALFORMED_OUTPUT,
            error=f"could not read docker info output: {exc}",
        )

    # `docker info` exits ZERO when the daemon is unreachable, reporting the
    # failure in `ServerErrors` and returning client-side data only. Measured on
    # this host: `DOCKER_HOST=tcp://127.0.0.1:1 docker info --format '{{json .}}'`
    # gives exit 0 and a payload with no NCPU. Without this branch that is
    # diagnosed as MALFORMED_OUTPUT — sending whoever reads it to hunt a docker
    # version incompatibility when the answer is "start Docker".
    if payload.ServerErrors:
        return CapacityProbe(
            DockerProbeOutcome.DAEMON_UNREACHABLE,
            error="; ".join(payload.ServerErrors),
        )

    if payload.NCPU is None or payload.MemTotal is None:
        return CapacityProbe(
            DockerProbeOutcome.MALFORMED_OUTPUT,
            error="docker info reported neither an error nor NCPU/MemTotal",
        )
    ncpu, mem_total = payload.NCPU, payload.MemTotal

    if ncpu <= 0 or mem_total <= 0:
        return CapacityProbe(
            DockerProbeOutcome.MALFORMED_OUTPUT,
            error=f"docker info reported NCPU={ncpu}, MemTotal={mem_total}",
        )
    return CapacityProbe(DockerProbeOutcome.OK, ncpu=ncpu, mem_total_bytes=mem_total)


class _ProbeCache:
    """One cached `docker info`, so a poll loop does not spawn one per call.

    Not `discovery_cache`'s shape only because this holds a single value with no
    key. Locked because the reconcile timer and an MCP handler can refresh it at
    the same moment.
    """

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._probe: CapacityProbe | None = None
        self._at: datetime | None = None

    def get(self, *, invoke: DockerInvoke, now: datetime, ttl: float) -> CapacityProbe:
        with self._lock:
            fresh = (
                self._probe is not None
                and self._at is not None
                and (now - self._at) < timedelta(seconds=ttl)
            )
            if fresh and self._probe is not None:
                return self._probe
        probe = probe_docker_info(invoke=invoke)
        with self._lock:
            self._probe = probe
            self._at = now
        return probe

    def clear(self) -> None:
        with self._lock:
            self._probe = None
            self._at = None


_CACHE = _ProbeCache()


def clear_probe_cache() -> None:
    """Drop the cached `docker info`. For tests and for the doctor's re-check."""
    _CACHE.clear()


def _derived(probe: CapacityProbe, *, now: datetime) -> Ceiling:
    headroom = settings.docker_capacity_headroom
    cpus = max(0.0, round(probe.ncpu * headroom - settings.docker_reserved_cpus, 2))
    memory_mb = max(
        0,
        int(probe.mem_total_bytes / _BYTES_PER_MB * headroom) - settings.docker_reserved_memory_mb,
    )
    return Ceiling(
        cpus=cpus,
        memory_mb=memory_mb,
        leases=settings.docker_capacity_max_leases,
        source=DockerCeilingSource.PROBE,
        probed_at=now,
    )


def resolve_ceiling(
    *,
    invoke: DockerInvoke = run_docker,
    now: datetime | None = None,
    previous: Ceiling | None = None,
    use_cache: bool = True,
) -> Ceiling:
    """The ceiling to enforce, and where it came from.

    In order:

    1. **Both overrides set** — the operator has stated the limit; do not probe.
    2. **Probe succeeds** — headroom fraction of the machine, less the reserved
       baseline.
    3. **Probe fails, a previous ceiling exists** — keep it, marked
       `STALE_PROBE` and carrying the error. Reservations keep working and
       nobody is told a stale number is a fresh one.
    4. **Probe fails, nothing was ever measured** — `UNKNOWN`, zeroes, and
       admission refuses. Fail closed: a ledger that admits freely because it
       cannot see the machine is worse than one that says it cannot see it.
    """
    stamp = now or datetime.now(timezone.utc)

    if settings.docker_capacity_cpus > 0 and settings.docker_capacity_memory_mb > 0:
        return Ceiling(
            cpus=settings.docker_capacity_cpus,
            memory_mb=settings.docker_capacity_memory_mb,
            leases=settings.docker_capacity_max_leases,
            source=DockerCeilingSource.CONFIG_OVERRIDE,
            probed_at=stamp,
        )

    if use_cache:
        probe = _CACHE.get(invoke=invoke, now=stamp, ttl=settings.docker_info_cache_seconds)
    else:
        probe = probe_docker_info(invoke=invoke)

    if probe.ok:
        return _derived(probe, now=stamp)

    logger.warning(
        "docker info failed (%s): %s; capacity ceiling falls back to %s",
        probe.outcome.value,
        probe.error,
        "the last measurement" if previous and previous.known else "unknown",
    )
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
        cpus=0.0,
        memory_mb=0,
        leases=0,
        source=DockerCeilingSource.UNKNOWN,
        probed_at=None,
        error=probe.error,
    )
