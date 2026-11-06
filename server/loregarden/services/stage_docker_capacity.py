"""Resolving the docker footprint a stage declared, and holding it for the run.

A thin seam between two subsystems that should not import each other:
`docker_leases` knows capacity and nothing about workflow templates, and the
orchestrator knows stages and nothing about pools. This module knows both, and
is the only place that does.

Its other job is to fail *safely* in the direction that costs least. A stage
whose template cannot be resolved — an instance that vanished, a version pinned
to a snapshot that is gone — takes no lease and runs. Refusing to run a stage
because its footprint could not be looked up would turn a lookup problem into an
outage, and the overwhelmingly common answer is `NONE` anyway. The lookup
failure is logged rather than swallowed, because a stage that silently stopped
booking capacity is exactly the drift this ledger exists to prevent.
"""

from __future__ import annotations

import logging
from collections.abc import Iterator
from contextlib import contextmanager, nullcontext

from loregarden.config import settings
from loregarden.core.workflow_loader import get_template_stages_at_version
from loregarden.models.domain import (
    AgentRun,
    DockerFootprint,
    DockerGrantState,
    DockerHolderKind,
    DockerLeaseEndReason,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
)
from loregarden.services.capacity_errors import CapacityUnavailable
from loregarden.services.capacity_line import renew_orchestration_lease, wait_while_line_moves
from loregarden.services.docker_leases import DockerReservation, release_lease, reserve
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


class DockerCapacityUnavailable(CapacityUnavailable):
    """A stage that declared a docker footprint could not get one.

    Raised rather than returned, and rather than letting the stage run
    anyway: a ledger a caller may ignore when it is inconvenient is a report,
    not a limit. The run fails with a message naming what it wanted, which
    the existing stage-retry path handles as a dispatch failure.
    """


@contextmanager
def docker_capacity_for_stage(
    session: Session,
    run: AgentRun,
    stage: WorkflowStageDef,
    *,
    wait_seconds: float | None = None,
) -> Iterator[DockerReservation | None]:
    """Hold docker capacity for as long as this stage's agent is executing.

    Yields `None` — immediately, and after one enum comparison — for a stage
    that declared no footprint. That is nearly every stage, and it is why the
    opt-in is per stage rather than per workspace: a lease held by a planning
    stage is capacity taken from the test stage waiting behind it.

    Scoped to the same block `run_lease.lease_renewal` wraps, deliberately. That
    contextmanager exists because the supervising thread is the one thing that
    means "this run is alive" across all three execution paths; a docker lease
    with the same lifetime inherits that liveness instead of carrying a second
    heartbeat that can drift out of step with the first. The reaper knows: a
    lease bound to a live run is extended without asking docker at all.

    **The stage waits while the line moves, and fails rather than proceeding when
    it stalls.** `docker_stage_wait_seconds` bounds the time nothing ahead finishes,
    not the whole wait (`capacity_line`). The
    orchestrator is a background thread, so it may block where an MCP handler
    may not — but running the stage anyway when capacity was refused would make
    the ledger advisory, and a ledger nobody has to obey is a report, not a
    limit.
    """
    footprint = stage.docker_footprint
    if footprint is DockerFootprint.NONE or not settings.docker_capacity_enabled:
        yield None
        return

    budget = settings.docker_stage_wait_seconds if wait_seconds is None else wait_seconds
    reservation = reserve(
        session,
        holder_label=f"stage {stage.key} ({run.agent_id})",
        footprint=footprint,
        cpus=stage.docker_cpus,
        memory_mb=stage.docker_memory_mb,
        holder_kind=DockerHolderKind.AGENT_RUN,
        agent_run_id=run.id,
        orchestration_run_id=run.orchestration_run_id,
        ticket_id=run.ticket_id,
        workspace_id=run.workspace_id,
    )

    if reservation.state is DockerGrantState.QUEUED:
        orchestration_run_id = run.orchestration_run_id
        reservation = wait_while_line_moves(
            session,
            reservation,
            stall_seconds=budget,
            on_poll=(
                (lambda: renew_orchestration_lease(session, orchestration_run_id))
                if orchestration_run_id
                else None
            ),
        )

    if not reservation.granted:
        # Give the place in line back rather than leaving a waiter nobody will
        # ever poll for — it would sit at the head of the queue until the
        # reaper's abandonment sweep, blocking everything behind it.
        if reservation.lease_id:
            release_lease(session, reservation.lease_id, reason=DockerLeaseEndReason.ABANDONED)
        raise DockerCapacityUnavailable(
            f"Stage {stage.key} needs {footprint.value} docker capacity and did not get it: "
            f"nothing ahead in line finished for {budget:.0f}s — {reservation.message}"
        )

    try:
        yield reservation
    finally:
        reservation.release(reason=DockerLeaseEndReason.RUN_COMPLETED)


def stage_def_for_run(session: Session, run: AgentRun) -> WorkflowStageDef | None:
    """The stage definition this run is executing, or None if it cannot be resolved.

    None is not an error path the caller must handle specially — see the module
    docstring — but it is logged, because "this stage declares no footprint" and
    "nobody could work out what this stage declares" are different facts.
    """
    stage_key = (run.stage_key or "").strip()
    if not stage_key or not run.ticket_id:
        return None

    instance = session.exec(
        select(WorkflowInstance).where(WorkflowInstance.ticket_id == run.ticket_id)
    ).first()
    if instance is None:
        logger.warning(
            "Run %s (ticket %s) has no workflow instance; assuming no docker footprint",
            run.id,
            run.ticket_id,
        )
        return None

    template = session.get(WorkflowTemplate, instance.template_id)
    if template is None:
        logger.warning(
            "Workflow instance for ticket %s names template %s, which is missing; "
            "assuming no docker footprint",
            run.ticket_id,
            instance.template_id,
        )
        return None

    stages = get_template_stages_at_version(session, template, instance.template_version)
    for stage in stages:
        if stage.key == stage_key:
            return stage
    logger.warning(
        "Stage %r is not in template %s; assuming no docker footprint",
        stage_key,
        template.slug,
    )
    return None


@contextmanager
def stage_docker_capacity(session: Session, run: AgentRun) -> Iterator[DockerReservation | None]:
    """Hold whatever docker capacity this run's stage declared, for its duration.

    Wraps the same block `run_lease.lease_renewal` does, so the lease inherits
    the one signal that means "this run is still alive" on every execution path.
    Yields `None` for the overwhelmingly common stage that declared nothing.
    """
    stage = stage_def_for_run(session, run)
    if stage is None or stage.docker_footprint is DockerFootprint.NONE:
        with nullcontext():
            yield None
        return
    with docker_capacity_for_stage(session, run, stage) as reservation:
        yield reservation
