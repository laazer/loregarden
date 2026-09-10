"""The doctor's view of the docker capacity ledger.

Its own module: `doctor.py` is 636 lines of environment checks that all shell
out to git or stat the tree, and this one reaches a different subsystem
entirely. It is registered into that module's `CHECKS` table.

What it is actually for. The reaper is careful never to reclaim a lease it could
not verify — which is right, and which means a docker outage produces *silence*:
nothing is reclaimed, nothing fails, and the pool slowly fills with leases whose
holders are long gone. A sweep that has quietly stopped reaping looks exactly
like one with nothing to do. This is the thing that tells them apart.

Deliberately NOT in `DISPATCH_PREFLIGHT_CHECKS`. It shells out to docker, and
that module's own docstring is explicit that a preflight adding a second to
every dispatch is a preflight somebody turns off.
"""

from __future__ import annotations

from pathlib import Path

from loregarden.config import settings
from loregarden.models.domain import (
    DockerCeilingSource,
    DoctorCheck,
    DoctorFinding,
    DoctorStatus,
    Workspace,
)
from loregarden.services.docker_board import capacity_status
from loregarden.services.docker_capacity import DockerInvoke, resolve_ceiling
from loregarden.services.docker_ledger import load_pool, pool_ceiling
from loregarden.services.docker_subprocess import run_docker
from sqlmodel import Session


def check_docker_capacity(
    session: Session,
    workspace: Workspace,
    repo_root: Path,
    *,
    invoke: DockerInvoke = run_docker,
) -> DoctorFinding:
    """Whether the ledger can see the machine it is booking, and reap what it books.

    Measures rather than reporting the last stored number — this is the
    on-demand check, and answering "the ceiling is unknown" from a row nobody
    has refreshed would describe the database instead of the machine. It does
    not persist what it finds: a doctor check that writes would make reading the
    report change the thing being reported.

    `workspace` and `repo_root` are unused: capacity belongs to the machine, not
    to a workspace, exactly as the agent slot pool does. The signature is the
    table's.
    """
    if not settings.docker_capacity_enabled:
        return DoctorFinding(
            check=DoctorCheck.DOCKER_CAPACITY,
            status=DoctorStatus.PASS,
            finding="The docker capacity ledger is disabled; nothing is being tracked.",
        )

    stored = pool_ceiling(load_pool(session))
    ceiling = resolve_ceiling(invoke=invoke, previous=stored, use_cache=False)
    board = capacity_status(session)
    unverifiable = board["unverifiable"]
    in_use = board["in_use"]["leases"]
    waiting = len(board["waiting"])

    if unverifiable:
        errors = "; ".join(
            f"{entry['lease_id'][:8]} ({entry['outcome']}: {entry['error']})"
            for entry in unverifiable[:3]
        )
        return DoctorFinding(
            check=DoctorCheck.DOCKER_CAPACITY,
            status=DoctorStatus.FAIL,
            finding=(
                f"{len(unverifiable)} docker lease(s) cannot be verified, so the reaper "
                f"is holding their capacity rather than guessing: {errors}"
            ),
            remediation=(
                "Start Docker and let the next reconciliation sweep settle them, or "
                "clear a lease whose owner is known to be gone with "
                "loregarden_force_release_docker_lease."
            ),
        )

    if ceiling.source is DockerCeilingSource.UNKNOWN:
        # FAIL only when something is actually being refused. On a machine with
        # no Docker and nothing asking for it, an unmeasured ceiling is not a
        # fault — it is an unused subsystem, and failing here would turn the
        # doctor red for every workspace that never touches containers.
        idle = in_use == 0 and waiting == 0
        return DoctorFinding(
            check=DoctorCheck.DOCKER_CAPACITY,
            status=DoctorStatus.WARN if idle else DoctorStatus.FAIL,
            finding=(
                "Docker capacity cannot be measured"
                + (
                    ", and nothing is asking for it."
                    if idle
                    else f", so {in_use + waiting} request(s) are being refused."
                )
                + f" {ceiling.error or 'docker info did not succeed'}"
            ),
            remediation=(
                "Start Docker, or set LOREGARDEN_DOCKER_CAPACITY_CPUS and "
                "LOREGARDEN_DOCKER_CAPACITY_MEMORY_MB to state the limit yourself."
            ),
        )

    if ceiling.source is DockerCeilingSource.STALE_PROBE:
        return DoctorFinding(
            check=DoctorCheck.DOCKER_CAPACITY,
            status=DoctorStatus.WARN,
            finding=(
                f"The capacity ceiling ({ceiling.cpus} cpus / {ceiling.memory_mb} MB) is "
                f"the last good measurement; docker is not answering now: {ceiling.error}"
            ),
            remediation="Start Docker. Reservations keep working against the old number meanwhile.",
        )

    if board["orphaned"]:
        return DoctorFinding(
            check=DoctorCheck.DOCKER_CAPACITY,
            status=DoctorStatus.WARN,
            finding=(
                f"{len(board['orphaned'])} docker lease(s) expired with containers still "
                "running. The capacity is still held, because it is still being used."
            ),
            remediation=(
                "Stop the stale stack yourself — this ledger never runs `docker compose "
                "down` — or renew the lease if the work is still wanted."
            ),
        )

    return DoctorFinding(
        check=DoctorCheck.DOCKER_CAPACITY,
        status=DoctorStatus.PASS,
        finding=(
            f"Docker capacity: {board['in_use']['cpus']}/{ceiling.cpus} cpus, "
            f"{in_use}/{ceiling.leases} leases, "
            f"{waiting} waiting."
        ),
    )
