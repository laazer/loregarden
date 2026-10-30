"""What a command holding a capacity lease learns about its grant.

Split out of `capacity_run` so a caller that only needs to hand a child its
grant does not also take on the queue-waiting machinery. That machinery reaches
`docker_reaper`, which reaches `parallel_queue` and from there the whole
orchestrator, so a module on the landing path importing `capacity_run` closes a
cycle back into `orchestration` (lg-milestone-that-1238):

    landing -> land_ticket -> land_verification -> gate_capacity ->
    capacity_run -> docker_reaper -> parallel_queue -> ... -> orchestration

The direction is what was wrong, not the import: deciding a child's environment
from a granted reservation is a leaf concern, and the reaping of stale leases by
a process that happens to be waiting in line is not. Naming the four environment
variables and translating a grant into them is the whole of this module, and it
depends on nothing but the reservation it is handed.
"""

from __future__ import annotations

from collections.abc import Mapping

from loregarden.services.docker_leases import DockerReservation

#: What the command learns about its grant. Workers is the cpus rounded down,
#: never below one — what a test runner's `-n` / `--maxWorkers` should be.
LEASE_ENV = "LOREGARDEN_CAPACITY_LEASE_ID"
CPUS_ENV = "LOREGARDEN_CAPACITY_CPUS"
WORKERS_ENV = "LOREGARDEN_CAPACITY_WORKERS"

#: Where the caller stood before `scripts/loregarden-cli.sh` moved into
#: `server/` to start uv. The held command runs there, so a relative path in it
#: means what the caller meant. The command does not inherit it: a nested
#: `loregarden` started some other way, from another directory, would otherwise
#: run its own command where the outer caller stood.
CALLER_CWD_ENV = "LOREGARDEN_CALLER_CWD"

#: The repo root this process resolves its database through. The CLI defaults it
#: to the primary checkout, which is right for the ledger and wrong for the held
#: command: tests run from a worktree would read the primary's `agent_context/`.
REPO_ROOT_ENV = "LOREGARDEN_REPO_ROOT"

#: The caller's own `LOREGARDEN_REPO_ROOT` as `scripts/loregarden-cli.sh` found
#: it, "" when it had none. The held command gets that back. Absent when the CLI
#: was started some other way, and then the environment is left as it is.
CALLER_REPO_ROOT_ENV = "LOREGARDEN_CALLER_REPO_ROOT"


def worker_count(cpus: float) -> int:
    return max(1, int(cpus))


def child_environment(reservation: DockerReservation, base: Mapping[str, str]) -> dict[str, str]:
    env = dict(base)
    env.pop(CALLER_CWD_ENV, None)
    if CALLER_REPO_ROOT_ENV in env:
        caller_root = env.pop(CALLER_REPO_ROOT_ENV)
        if caller_root:
            env[REPO_ROOT_ENV] = caller_root
        else:
            env.pop(REPO_ROOT_ENV, None)
    env[LEASE_ENV] = reservation.lease_id
    env[CPUS_ENV] = f"{reservation.cpus:g}"
    env[WORKERS_ENV] = str(worker_count(reservation.cpus))
    return env
