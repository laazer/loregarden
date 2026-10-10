"""`loregarden local_main` — the main loregarden server running on this machine.

    loregarden local_main

Reads the local instance registry (`~/.lore-eden/instances`, which `task server`
registers itself in), not the database, so it works from any worktree and
migrates nothing. Prints the registered record and a live health probe as JSON;
exits 1 when no main server is running.
"""

from __future__ import annotations

import argparse
import json

from loregarden.services.local_instances import PROJECT, get_instance_manager, get_registry


class LocalMainNotRunningError(RuntimeError):
    """No live main server is registered for loregarden."""


def _local_main(_args: argparse.Namespace) -> str:
    record = get_registry().find_main(PROJECT)
    if record is None:
        raise LocalMainNotRunningError(
            "no main loregarden server is registered on this machine. "
            "Start one with `task server` (it registers itself)."
        )
    manager = get_instance_manager()
    payload = {
        "instance": manager.get(record.id).model_dump(mode="json"),
        "health": manager.health(record.id).model_dump(mode="json"),
    }
    return json.dumps(payload, indent=2)


def register(groups: argparse._SubParsersAction) -> None:
    """Add `local_main` to the root CLI."""
    command = groups.add_parser(
        "local_main",
        help="Show the main loregarden server running on this machine (URL, pid, health).",
    )
    command.set_defaults(run=_local_main)
