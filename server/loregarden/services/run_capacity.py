"""The capacity an agent run holds while it executes: its stage's docker, and the host.

Every agent CLI is a process on this machine, and agent concurrency was bounded
only by lanes and slots — numbers that know nothing about what else the machine
is doing. Each run now holds a small host lease for exactly the block
`run_lease.lease_renewal` wraps, so it inherits the liveness that block already
means: the reaper extends a lease whose run is alive and reclaims one whose run
is not, on every execution path.

**Sized per runtime, from measurement.** `RUNTIME_WEIGHTS` below. The agent
process itself is light — the heavy work is the tools it runs, which take their
own leases through `capacity run` and the pre-push hook.

**Takes no lease-count slot.** The slots space out heavy work; a run's standing
claim must not crowd out the test suites they exist for. That is also what keeps
an agent's own push, which queues in the ordinary line, from deadlocking against
agents: with no slot taken, only the agents' cpus and memory could fill the
pool, and at these sizes that would take dozens of concurrent runs.

**Never waits while holding.** A stage that declared a docker footprint takes
that lease first (waiting, holding nothing), and the host lease is then a child
of it — granted from the docker lease's own host share, never queued.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Iterator
from contextlib import contextmanager

from loregarden.agents.registry import get_agent
from loregarden.config import settings
from loregarden.models.domain import (
    AgentRun,
    CapacityPool,
    CliAdapter,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLeaseEndReason,
    Ticket,
    Workspace,
)
from loregarden.services.capacity_children import reserve_child
from loregarden.services.cli_settings import (
    get_ticket_orchestration_runtime,
    resolve_effective_adapter,
)
from loregarden.services.docker_leases import (
    DockerReservation,
    release_lease,
    reserve,
    wait_for_grant,
)
from loregarden.services.stage_docker_capacity import stage_docker_capacity
from sqlmodel import Session

logger = logging.getLogger(__name__)

#: (cpus, memory_mb) a run on each runtime holds; None holds nothing.
#:
#: Calibrated 2026-10-03 on the development machine (10 cpus / 64 GB): 59
#: Claude Code CLI processes sampled every 5s for 120s. Mean cpu median 0.0%,
#: p90 0.3%, max 3.1%; peak cpu p90 2.9%, max 36%; RSS p90 318 MB, max 836 MB.
#: Claude is sized to its burst (a quarter core) and its worst observed memory.
#: Cursor, Codex and OpenCode had no live process to measure and carry Claude's
#: numbers PROVISIONALLY — re-measure before trusting them. LM Studio runs
#: inference on this machine, so it is sized as a stack until it is measured.
#: LOCAL is the in-process test runner and spawns nothing.
RUNTIME_WEIGHTS: dict[CliAdapter, tuple[float, int] | None] = {
    CliAdapter.CLAUDE: (0.25, 768),
    CliAdapter.CURSOR: (0.25, 768),
    CliAdapter.CODEX: (0.25, 768),
    CliAdapter.OPENCODE: (0.25, 768),
    CliAdapter.LMSTUDIO: (2.0, 4096),
    CliAdapter.LOCAL: None,
}


class HostCapacityUnavailable(RuntimeError):
    """A run could not get its host lease. Raised so the run fails, naming why."""


def runtime_for_run(session: Session, run: AgentRun) -> CliAdapter:
    """The adapter this run will execute on, resolved the way the executor does."""
    agent = get_agent(run.agent_id) or {}
    ticket = session.get(Ticket, run.ticket_id) if run.ticket_id else None
    workspace = session.get(Workspace, run.workspace_id) if run.workspace_id else None
    adapter = resolve_effective_adapter(
        agent_adapter=agent.get("adapter", CliAdapter.LOCAL.value),
        workspace=workspace,
        ticket_adapter=(
            get_ticket_orchestration_runtime(ticket).cli_adapter
            if ticket is not None
            else CliAdapter.DEFAULT
        ),
    )
    try:
        return CliAdapter(adapter)
    except ValueError:
        logger.warning(
            "Run %s resolves to unknown adapter %r; sizing its host lease as Claude's",
            run.id,
            adapter,
        )
        return CliAdapter.CLAUDE


@contextmanager
def run_capacity(session: Session, run: AgentRun) -> Iterator[None]:
    """Hold the stage's docker footprint, then the run's host lease, for the block."""
    with (
        stage_docker_capacity(session, run) as docker,
        host_capacity_for_run(session, run, docker_parent=docker),
    ):
        yield


@contextmanager
def host_capacity_for_run(
    session: Session,
    run: AgentRun,
    *,
    docker_parent: DockerReservation | None = None,
    wait_seconds: float | None = None,
) -> Iterator[DockerReservation | None]:
    """Hold this run's host lease for the block; yields None when it takes none."""
    if not (settings.docker_capacity_enabled and settings.agent_run_capacity_enabled):
        yield None
        return
    weights = RUNTIME_WEIGHTS.get(runtime_for_run(session, run))
    if weights is None:
        yield None
        return

    reservation = _acquire(session, run, weights, docker_parent, wait_seconds)
    try:
        yield reservation
    finally:
        release_lease(session, reservation.lease_id, reason=DockerLeaseEndReason.RUN_COMPLETED)


def _acquire(
    session: Session,
    run: AgentRun,
    weights: tuple[float, int],
    docker_parent: DockerReservation | None,
    wait_seconds: float | None,
) -> DockerReservation:
    cpus, memory_mb = weights
    label = f"agent run {run.agent_id} · stage {run.stage_key or '-'}"
    common = {
        "holder_label": label,
        "footprint": DockerFootprint.CUSTOM,
        "cpus": cpus,
        "memory_mb": memory_mb,
        "holder_kind": DockerHolderKind.AGENT_RUN,
        "agent_run_id": run.id,
        "orchestration_run_id": run.orchestration_run_id,
        "ticket_id": run.ticket_id,
        "workspace_id": run.workspace_id,
        "holder_pid": os.getpid(),
    }
    if docker_parent is not None and docker_parent.granted:
        reservation = reserve_child(
            session,
            parent_lease_id=docker_parent.lease_id,
            pool=CapacityPool.HOST,
            **common,
        )
    else:
        reservation = reserve(session, pool=CapacityPool.HOST, takes_slot=False, **common)
        if reservation.state is DockerGrantState.QUEUED:
            budget = (
                settings.agent_run_capacity_wait_seconds if wait_seconds is None else wait_seconds
            )
            logger.warning(
                "Run %s is waiting up to %.0fs for host capacity: %s",
                run.id,
                budget,
                reservation.message,
            )
            reservation = wait_for_grant(session, reservation, budget=budget)

    if reservation.granted:
        return reservation
    if reservation.lease_id:
        # Give the place in line back rather than leave a waiter nobody polls.
        release_lease(session, reservation.lease_id, reason=DockerLeaseEndReason.ABANDONED)
    raise HostCapacityUnavailable(
        f"Run {run.id} ({label}) did not get {cpus:g} cpus / {memory_mb} MB of host "
        f"capacity: {reservation.error_kind or 'still queued'} — {reservation.message}"
    )
