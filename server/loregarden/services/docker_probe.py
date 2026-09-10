"""Asking docker whether a lease's containers are still running.

The whole module exists because of one measured fact about the docker CLI,
confirmed against the daemon on this machine:

    docker ps --filter label=…  --format '{{.ID}}'   → exit 0, empty stdout
    DOCKER_HOST=tcp://127.0.0.1:1 docker ps          → exit 1, empty stdout

"Nothing matched" and "I could not ask" print exactly the same thing. A probe
returning a count, or a bool, cannot express the difference — and the caller
that cannot express it is the reaper, whose two answers are "free this capacity"
and "leave it alone". Collapsing them would free every lease on the box the
moment Docker Desktop restarted.

So the result is a three-state, and `UNKNOWN` is never treated as `GONE`
anywhere downstream.
"""

from __future__ import annotations

import json
import logging
import subprocess
from collections.abc import Sequence
from dataclasses import dataclass

from loregarden.models.domain.enums import DockerLivenessState, DockerProbeOutcome
from loregarden.services.docker_capacity import DockerInvoke
from loregarden.services.docker_subprocess import DockerVerbRefused, run_docker

logger = logging.getLogger(__name__)

#: The label compose stamps on every container it creates. Filtering on it is
#: how a lease that named a project finds its containers without having recorded
#: their names — which it cannot have, since compose picks them.
COMPOSE_PROJECT_LABEL = "com.docker.compose.project"


@dataclass(frozen=True)
class DockerLiveness:
    """What a probe established, and why it established nothing when it failed."""

    state: DockerLivenessState
    running: int = 0
    outcome: DockerProbeOutcome = DockerProbeOutcome.OK
    error: str = ""


def run_probe(args: Sequence[str], *, invoke: DockerInvoke) -> tuple[str, DockerProbeOutcome, str]:
    """`(stdout, outcome, error)` — every failure mode named rather than raised.

    Public because two callers now need the same three-state reading of a docker
    query: liveness here, and the unaccounted-container report. A second copy of
    this would be a second place for "empty output" to quietly mean "nothing
    running" when it meant "could not ask".
    """
    try:
        result = invoke(list(args))
    except FileNotFoundError as exc:
        return ("", DockerProbeOutcome.BINARY_MISSING, str(exc))
    except subprocess.TimeoutExpired as exc:
        return ("", DockerProbeOutcome.TIMED_OUT, str(exc))
    except DockerVerbRefused as exc:  # pragma: no cover — a programming error
        return ("", DockerProbeOutcome.REFUSED_VERB, str(exc))

    if result.returncode != 0:
        error = (result.stderr or "").strip() or f"docker exited {result.returncode}"
        return ("", DockerProbeOutcome.DAEMON_UNREACHABLE, error)
    return (result.stdout or "", DockerProbeOutcome.OK, "")


def probe_compose_project(project: str, *, invoke: DockerInvoke = run_docker) -> DockerLiveness:
    """How many of `project`'s containers are running.

    `docker ps` without `-a`, so it lists running containers only: an exited
    container is not capacity being used, and counting it would hold a lease
    open against a stack that has already stopped.
    """
    stdout, outcome, error = run_probe(
        ["ps", "--filter", f"label={COMPOSE_PROJECT_LABEL}={project}", "--format", "{{.ID}}"],
        invoke=invoke,
    )
    if outcome is not DockerProbeOutcome.OK:
        return DockerLiveness(DockerLivenessState.UNKNOWN, outcome=outcome, error=error)

    running = len([line for line in stdout.splitlines() if line.strip()])
    state = DockerLivenessState.ALIVE if running else DockerLivenessState.GONE
    return DockerLiveness(state, running=running)


def probe_containers(names: Sequence[str], *, invoke: DockerInvoke = run_docker) -> DockerLiveness:
    """How many of `names` are running.

    A container that no longer exists is not an error: `docker inspect` reports
    it on stderr and exits non-zero even when other arguments resolved, so the
    running states are read from stdout and a partial answer is still an answer.
    A run where *nothing* parsed and the command failed is `UNKNOWN`.
    """
    if not names:
        return DockerLiveness(DockerLivenessState.GONE)

    try:
        result = invoke(["inspect", "--format", "{{json .State.Running}}", *names])
    except FileNotFoundError as exc:
        return DockerLiveness(
            DockerLivenessState.UNKNOWN, outcome=DockerProbeOutcome.BINARY_MISSING, error=str(exc)
        )
    except subprocess.TimeoutExpired as exc:
        return DockerLiveness(
            DockerLivenessState.UNKNOWN, outcome=DockerProbeOutcome.TIMED_OUT, error=str(exc)
        )
    except DockerVerbRefused as exc:  # pragma: no cover — a programming error
        return DockerLiveness(
            DockerLivenessState.UNKNOWN, outcome=DockerProbeOutcome.REFUSED_VERB, error=str(exc)
        )

    states: list[bool] = []
    for line in (result.stdout or "").splitlines():
        token = line.strip()
        if not token:
            continue
        try:
            states.append(bool(json.loads(token)))
        except ValueError:
            return DockerLiveness(
                DockerLivenessState.UNKNOWN,
                outcome=DockerProbeOutcome.MALFORMED_OUTPUT,
                error=f"docker inspect printed {token!r}, which is not a running state",
            )

    if not states:
        stderr = (result.stderr or "").strip()
        if result.returncode != 0 and "No such object" not in stderr:
            # The command failed for a reason other than the containers being
            # gone, so nothing was established. Not "gone".
            return DockerLiveness(
                DockerLivenessState.UNKNOWN,
                outcome=DockerProbeOutcome.DAEMON_UNREACHABLE,
                error=stderr or f"docker inspect exited {result.returncode}",
            )
        return DockerLiveness(DockerLivenessState.GONE)

    running = sum(1 for state in states if state)
    state = DockerLivenessState.ALIVE if running else DockerLivenessState.GONE
    return DockerLiveness(state, running=running)


def probe_lease_liveness(
    *,
    compose_project: str,
    container_names: Sequence[str],
    invoke: DockerInvoke = run_docker,
) -> DockerLiveness:
    """Whether anything this lease named is still running.

    Both sources are consulted when both are recorded, and the combination is
    deliberately generous in the direction that does not free capacity: any
    `ALIVE` wins, and an `UNKNOWN` beats a `GONE`. A lease is only reclaimed
    when everything it named was successfully asked about and none of it is
    running.
    """
    results: list[DockerLiveness] = []
    if compose_project:
        results.append(probe_compose_project(compose_project, invoke=invoke))
    if container_names:
        results.append(probe_containers(list(container_names), invoke=invoke))
    if not results:
        return DockerLiveness(DockerLivenessState.GONE)

    alive = [r for r in results if r.state is DockerLivenessState.ALIVE]
    if alive:
        return DockerLiveness(DockerLivenessState.ALIVE, running=sum(r.running for r in alive))
    unknown = [r for r in results if r.state is DockerLivenessState.UNKNOWN]
    if unknown:
        return unknown[0]
    return DockerLiveness(DockerLivenessState.GONE)
