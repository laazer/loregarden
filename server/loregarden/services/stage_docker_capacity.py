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

from loregarden.core.workflow_loader import get_template_stages_at_version
from loregarden.models.domain import (
    AgentRun,
    DockerFootprint,
    WorkflowInstance,
    WorkflowStageDef,
    WorkflowTemplate,
)
from loregarden.services.docker_leases import DockerReservation, docker_capacity_for_stage
from sqlmodel import Session, select

logger = logging.getLogger(__name__)


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
