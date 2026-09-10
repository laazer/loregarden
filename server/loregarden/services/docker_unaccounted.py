"""Containers running on this machine that no lease accounts for.

The ledger is advisory. It refuses to over-book what it knows about, and it
knows about exactly what callers told it — so a caller that skips it is invisible
to every other part of this feature. A ledger nobody uses and a ledger everybody
uses look identical from the inside: both report capacity available and both
reap nothing. That is the failure mode this module exists to make visible.

**It never reclaims and never stops anything.** This is a report. An unaccounted
container is usually not a fault at all — it is a database somebody runs all the
time — and the only honest response is to name it and let a human decide whether
it belongs in the baseline or whether something skipped the ledger.

**Which is why the baseline is explicit.** There is no age heuristic and no
guess about what "looks like agent work". A standing container reported as a
violation every sweep forever is noise, and noise is how a check gets ignored;
so the operator names their long-lived projects in config once, and everything
else is genuinely unexpected. The first run listing the standing baseline is not
a false positive — it is the prompt to configure it.

**A probe that fails reports nothing found and says so.** Empty output from
`docker ps` means "nothing matched" or "could not ask", and collapsing them here
would turn a stopped daemon into a clean bill of health.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from loregarden.config import settings
from loregarden.models.domain import DockerLease, DockerProbeOutcome
from loregarden.services.docker_capacity import DockerInvoke
from loregarden.services.docker_ledger import OCCUPYING, container_names
from loregarden.services.docker_probe import COMPOSE_PROJECT_LABEL, run_probe
from loregarden.services.docker_subprocess import run_docker
from pydantic import BaseModel, ConfigDict, TypeAdapter, ValidationError
from sqlmodel import Session, col, select

logger = logging.getLogger(__name__)


class RunningContainer(BaseModel):
    """One line of `docker ps --format '{{json .}}'`.

    Modelled rather than read out of a dict: it is a third-party payload, and
    `Labels` is a comma-joined `k=v` string that has to be parsed before the
    compose project can be read off it.
    """

    model_config = ConfigDict(extra="ignore")

    Names: str = ""
    Labels: str = ""
    CreatedAt: str = ""
    Image: str = ""

    @property
    def compose_project(self) -> str:
        for pair in self.Labels.split(","):
            key, _, value = pair.partition("=")
            if key.strip() == COMPOSE_PROJECT_LABEL:
                return value.strip()
        return ""


_CONTAINER = TypeAdapter(RunningContainer)


@dataclass
class UnaccountedReport:
    """What is running that nothing booked, or why that could not be established."""

    containers: list[RunningContainer] = field(default_factory=list)
    outcome: DockerProbeOutcome = DockerProbeOutcome.OK
    error: str = ""

    @property
    def ok(self) -> bool:
        return self.outcome is DockerProbeOutcome.OK

    @property
    def projects(self) -> list[str]:
        """Distinct compose projects among the unaccounted containers."""
        seen: list[str] = []
        for container in self.containers:
            project = container.compose_project
            if project and project not in seen:
                seen.append(project)
        return seen

    def as_dict(self) -> dict:
        return {
            "ok": self.ok,
            "outcome": self.outcome.value,
            "error": self.error,
            "containers": [
                {
                    "name": c.Names,
                    "compose_project": c.compose_project,
                    "image": c.Image,
                    "created_at": c.CreatedAt,
                }
                for c in self.containers
            ],
        }


def _baseline() -> tuple[set[str], set[str]]:
    """Projects and container names the operator has declared as theirs."""
    projects = {
        item.strip() for item in settings.docker_baseline_projects.split(",") if item.strip()
    }
    names = {
        item.strip() for item in settings.docker_baseline_containers.split(",") if item.strip()
    }
    return projects, names


def _accounted(session: Session) -> tuple[set[str], set[str]]:
    """Projects and container names some live lease already claims."""
    leases = session.exec(select(DockerLease).where(col(DockerLease.status).in_(OCCUPYING))).all()
    projects = {lease.compose_project for lease in leases if lease.compose_project}
    names: set[str] = set()
    for lease in leases:
        names.update(container_names(lease))
    return projects, names


def unaccounted_containers(
    session: Session, *, invoke: DockerInvoke = run_docker
) -> UnaccountedReport:
    """Running containers that no live lease and no configured baseline explains."""
    if not settings.docker_capacity_enabled:
        return UnaccountedReport()

    stdout, outcome, error = run_probe(["ps", "--format", "{{json .}}"], invoke=invoke)
    if outcome is not DockerProbeOutcome.OK:
        logger.warning("Could not list running containers (%s): %s", outcome.value, error)
        return UnaccountedReport(outcome=outcome, error=error)

    baseline_projects, baseline_names = _baseline()
    held_projects, held_names = _accounted(session)

    unaccounted: list[RunningContainer] = []
    for line in stdout.splitlines():
        if not line.strip():
            continue
        try:
            container = _CONTAINER.validate_json(line)
        except ValidationError:
            logger.warning("Could not read a `docker ps` line: %r", line[:200], exc_info=True)
            continue
        project = container.compose_project
        if project and (project in held_projects or project in baseline_projects):
            continue
        if container.Names and (container.Names in held_names or container.Names in baseline_names):
            continue
        unaccounted.append(container)

    return UnaccountedReport(containers=unaccounted)
