"""`loregarden git calls` — every place the code runs git or gh, and what it runs.

    loregarden git calls                      # all of them
    loregarden git calls --writes             # what moves or deletes a ref
    loregarden git calls --sub update-ref --sub push
    loregarden git calls --json

One line per command: the call site, the function it is in, the wrapper it goes
through, the argv (`…` where it is not a literal), and the `run_git` line that
runs it when that is inside a wrapper. See `services/git_call_index.py`.
"""

from __future__ import annotations

import argparse
import json
from dataclasses import asdict
from pathlib import Path

import loregarden
from loregarden.cli.errors import UsageError
from loregarden.services.git_call_index import GitCall, GitTool, index_git_calls

_PACKAGE = Path(loregarden.__file__).resolve().parent


def _line(call: GitCall) -> str:
    where = f"{call.path}:{call.line}"
    parts = [where, call.function, f"via {call.via}", f"{call.tool} {' '.join(call.argv)}"]
    if call.runs_at != where:
        parts.append(f"runs at {call.runs_at}")
    if call.ref_write:
        parts.append(f"WRITES {call.ref_write}")
    return "  ".join(parts)


def _calls(args: argparse.Namespace) -> str:
    root = Path(args.root).resolve() if args.root else _PACKAGE
    if not (root / "__init__.py").is_file():
        raise UsageError(f"{root} is not a package directory (no __init__.py)")
    calls = index_git_calls(root, include_tests=args.tests)
    if args.sub:
        calls = [c for c in calls if c.subcommand in args.sub]
    if args.writes:
        calls = [c for c in calls if c.ref_write]
    if args.tool:
        calls = [c for c in calls if c.tool is GitTool(args.tool)]
    if args.json:
        return json.dumps([asdict(c) for c in calls], indent=2)
    if not calls:
        return "no git or gh calls match"
    return "\n".join([*map(_line, calls), f"{len(calls)} calls"])


def register(sub: argparse._SubParsersAction) -> None:
    """Add `git calls` to the root CLI's `git` group."""
    calls = sub.add_parser(
        "calls", help="List every git/gh command the code runs, through its wrappers."
    )
    calls.add_argument(
        "--root", help="Top-level package directory to index (default: loregarden's own)."
    )
    calls.add_argument(
        "--sub", action="append", default=[], help="Only this subcommand; repeat for several."
    )
    calls.add_argument(
        "--writes", action="store_true", help="Only commands that move or delete a ref."
    )
    calls.add_argument("--tool", choices=[t.value for t in GitTool], help="Only git, or only gh.")
    calls.add_argument("--tests", action="store_true", help="Include test files.")
    calls.add_argument("--json", action="store_true", help="Emit JSON instead of lines.")
    calls.set_defaults(run=_calls)
