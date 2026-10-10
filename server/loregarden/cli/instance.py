"""`loregarden instance run` — run a command registered as a local instance.

    loregarden instance run --name client --kind client --port 5173 --health-path / \\
        -- npm run dev -- --port 5173 --strictPort

For startup tasks whose process cannot register itself (`task client`, `task
sandbox`): the record appears before the command starts and is removed when it
exits, so `python -m lore_eden.instances list`, the Instances page and the
launcher's port allocation all see it. The command's exit status is this
command's. See `services/local_instances.run_registered`.
"""

from __future__ import annotations

import argparse
import sys

from lore_eden.instances import InstanceKind, InstanceRole
from loregarden.cli.errors import UsageError
from loregarden.services.local_instances import SelfAdvertisement, run_registered


def _report(line: str) -> None:
    print(line, file=sys.stderr, flush=True)


def _labels(pairs: list[str]) -> dict[str, str]:
    labels: dict[str, str] = {}
    for pair in pairs:
        key, sep, value = pair.partition("=")
        if not sep or not key:
            raise UsageError(f"--label takes key=value, got {pair!r}")
        labels[key] = value
    return labels


def _run(args: argparse.Namespace) -> str:
    command = (
        args.wrapped_command[1:] if args.wrapped_command[:1] == ["--"] else args.wrapped_command
    )
    if not command:
        raise UsageError("give the command to run after `--`")
    advertisement = SelfAdvertisement(
        name=args.name,
        kind=InstanceKind(args.kind),
        role=InstanceRole(args.role),
        host=args.host,
        port=args.port,
        health_path=args.health_path,
        labels=_labels(args.label),
    )
    # The command's status, not EXIT_OK/EXIT_ERROR: a wrapper must see exactly
    # what the command returned.
    raise SystemExit(run_registered(advertisement, command, report=_report))


def register(groups: argparse._SubParsersAction) -> None:
    """Add `instance run` to the root CLI."""
    group = groups.add_parser("instance", help="Local instances started outside the launcher.")
    commands = group.add_subparsers(dest="command", required=True)
    run = commands.add_parser(
        "run", help="Run a command registered in the local instance registry."
    )
    run.add_argument("--name", required=True, help="Instance name; its id is loregarden-<name>.")
    run.add_argument("--kind", required=True, choices=[kind.value for kind in InstanceKind])
    run.add_argument(
        "--role",
        choices=[role.value for role in InstanceRole],
        default=InstanceRole.MAIN.value,
        help="main (default) for the shared instance; branch for one exercising a change.",
    )
    run.add_argument(
        "--host", default="127.0.0.1", help="Host the command binds (default 127.0.0.1)."
    )
    run.add_argument(
        "--port", required=True, type=int, help="Port the command binds; make it strict."
    )
    run.add_argument(
        "--health-path", default="/health", help="Path a health probe GETs (default /health)."
    )
    run.add_argument("--label", action="append", default=[], help="key=value, repeatable.")
    run.add_argument(
        "wrapped_command",
        metavar="command",
        nargs=argparse.REMAINDER,
        help="After `--`: the command to run.",
    )
    run.set_defaults(run=_run)
