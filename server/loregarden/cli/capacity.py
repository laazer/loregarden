"""`loregarden capacity run` — run a command while holding machine capacity.

    loregarden capacity run --footprint heavy --label "pre-push server-tests" -- pytest -n 4
    loregarden capacity run --cpus 2 --memory-mb 4096 --label build -- make

Waits in line when the machine is full, renews while the command runs, and
releases when it exits. The command's exit status is this command's. A
failure to get capacity exits 1 *before* the command starts; `--started-file`
is touched just before it does, so a wrapper can tell the two apart without
reserving an exit code the command might also use. See
`services/capacity_run.py`.
"""

from __future__ import annotations

import argparse
import os
import sys
from pathlib import Path

from loregarden.cli.errors import UsageError
from loregarden.db import session as db_session
from loregarden.models.domain import CapacityPool, DockerFootprint, Workspace
from loregarden.services.capacity_run import LEASE_ENV, CapacityRequest, run_holding
from loregarden.services.docker_capacity import CLASS_WEIGHTS
from sqlmodel import Session, select

#: Set by scripts/loregarden-cli.sh to the directory it was invoked from.
CALLER_CWD_ENV = "LOREGARDEN_CALLER_CWD"


def _report(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _workspace_id(slug: str | None) -> str | None:
    if not slug:
        return None
    with Session(db_session.engine) as session:
        workspace = session.exec(select(Workspace).where(Workspace.slug == slug)).first()
    if workspace is None:
        raise UsageError(f"no workspace with slug {slug!r}")
    return workspace.id


def _run(args: argparse.Namespace) -> str:
    command = args.held_command[1:] if args.held_command[:1] == ["--"] else args.held_command
    if not command:
        raise UsageError("give the command to run after `--`")
    if not args.footprint and not (args.cpus and args.memory_mb):
        raise UsageError("pass --footprint, or both --cpus and --memory-mb")

    db_session.init_db()
    request = CapacityRequest(
        label=args.label,
        pool=CapacityPool(args.pool),
        footprint=DockerFootprint(args.footprint) if args.footprint else DockerFootprint.CUSTOM,
        cpus=args.cpus,
        memory_mb=args.memory_mb,
        max_wait_seconds=args.max_wait,
        ttl_seconds=args.ttl,
        workspace_id=_workspace_id(args.workspace),
        parent_lease_id=args.parent_lease or None,
    )
    code = run_holding(
        lambda: Session(db_session.engine),
        request,
        command,
        report=_report,
        started_file=Path(args.started_file) if args.started_file else None,
        cwd=Path(args.cwd),
    )
    # The command's status, not EXIT_OK/EXIT_ERROR: a wrapper must see exactly
    # what the held command returned.
    raise SystemExit(code)


def register(sub: argparse._SubParsersAction) -> None:
    """Add `capacity run` to the root CLI's `capacity` group."""
    run = sub.add_parser("run", help="Run a command while holding machine capacity.")
    run.add_argument("--label", required=True, help="What is running, as the board shows it.")
    run.add_argument(
        "--footprint",
        choices=[footprint.value for footprint in CLASS_WEIGHTS],
        help="A named size; see services/docker_capacity.CLASS_WEIGHTS.",
    )
    run.add_argument("--cpus", type=float, default=0.0, help="Explicit cpus (with --memory-mb).")
    run.add_argument("--memory-mb", type=int, default=0, help="Explicit memory (with --cpus).")
    run.add_argument(
        "--pool",
        choices=[pool.value for pool in CapacityPool],
        default=CapacityPool.HOST.value,
        help="host (default) for processes; docker for containers, also charged to host.",
    )
    run.add_argument("--workspace", help="Workspace slug the work belongs to.")
    run.add_argument(
        "--max-wait",
        type=float,
        default=3600.0,
        help="Seconds to wait in line before giving up (default 3600).",
    )
    run.add_argument(
        "--parent-lease",
        default=os.environ.get(LEASE_ENV, ""),
        help=(
            "Nest under this lease: draw on its grant, never queue. Defaults to "
            f"${LEASE_ENV}, so a capacity run inside another nests automatically."
        ),
    )
    run.add_argument("--ttl", type=int, help="Lease TTL in seconds; renewed every third of it.")
    run.add_argument("--started-file", help="Touched just before the command starts.")
    run.add_argument(
        "--cwd",
        # scripts/loregarden-cli.sh cds into server/ to start Python, so the
        # process's own cwd is never the caller's; it records the caller's first.
        default=os.environ.get(CALLER_CWD_ENV) or os.getcwd(),
        help=f"Where the command runs. Defaults to ${CALLER_CWD_ENV}, else this process's cwd.",
    )
    run.add_argument(
        "held_command",
        metavar="command",
        nargs=argparse.REMAINDER,
        help="After `--`: the command to run.",
    )
    run.set_defaults(run=_run)
