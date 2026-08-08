"""`loregarden` — the control plane's command-line entry point.

One command for everything reachable without a running server: the MCP tool surface, the
stdio MCP proxy, and database setup. Subcommand modules register their own parsers here
and attach a `run(args) -> str` callable; this module owns argument parsing, output, and
the exit-code contract in `loregarden.cli.errors`.

    loregarden mcp list
    loregarden mcp call loregarden_get_ticket ticket_id=42
    loregarden mcp serve
    loregarden db init
"""

from __future__ import annotations

import argparse
import sys

from loregarden.cli import init_db, mcp_server, mcp_tools
from loregarden.cli.errors import EXIT_ERROR, EXIT_OK, EXIT_USAGE, UsageError


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="loregarden",
        description="Loregarden control plane CLI — works directly against the database.",
    )
    groups = parser.add_subparsers(dest="group", required=True)

    mcp_group = groups.add_parser("mcp", help="MCP tools and the stdio MCP proxy.")
    mcp_commands = mcp_group.add_subparsers(dest="command", required=True)
    mcp_tools.register(mcp_commands)
    mcp_server.register(mcp_commands)

    db_group = groups.add_parser("db", help="Database setup and maintenance.")
    db_commands = db_group.add_subparsers(dest="command", required=True)
    init_db.register(db_commands)

    return parser


def main(argv: list[str] | None = None) -> int:
    tokens = list(argv) if argv is not None else sys.argv[1:]
    args = build_parser().parse_args(mcp_tools.hoist_call_flags(tokens))
    try:
        output = args.run(args)
    except UsageError as exc:
        print(str(exc), file=sys.stderr)
        return EXIT_USAGE
    except Exception as exc:  # a failed operation is a result, not a crash
        print(f"{type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    if output:
        print(output)
    return EXIT_OK


if __name__ == "__main__":
    raise SystemExit(main())
