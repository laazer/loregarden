"""Stdio MCP proxy (`loregarden mcp serve`) — optional; prefer POST /mcp on the main server.

For one-off tool calls without a server, use `loregarden mcp call` instead of speaking
JSON-RPC at this proxy by hand.
"""

from __future__ import annotations

import argparse
import functools
import json
import os
import sys

import httpx
from loregarden.db import session as db_session
from loregarden.mcp.caller import (
    ORCHESTRATED_ENV,
    ORCHESTRATED_HEADER,
    RUN_ID_ENV,
    RUN_ID_HEADER,
)
from loregarden.mcp.protocol import handle_message
from sqlmodel import Session

API_BASE = os.environ.get("LOREGARDEN_API_BASE", "http://127.0.0.1:8000").rstrip("/")
USE_INPROCESS = os.environ.get("LOREGARDEN_MCP_INPROCESS", "").lower() in ("1", "true", "yes")
# Set only in the stdio env of a Loregarden-supervised run's --mcp-config
# (agents/mcp_context.py); mirrors the HTTP transport's headers (mcp/caller.py).
ORCHESTRATED = os.environ.get(ORCHESTRATED_ENV, "").lower() in ("1", "true", "yes")
RUN_ID = os.environ.get(RUN_ID_ENV, "") if ORCHESTRATED else ""


def _post_http(body: dict | list) -> dict | list:
    headers = {ORCHESTRATED_HEADER: "1"} if ORCHESTRATED else {}
    if RUN_ID:
        headers[RUN_ID_HEADER] = RUN_ID
    with httpx.Client(base_url=API_BASE, timeout=120.0) as client:
        res = client.post("/mcp", json=body, headers=headers)
        res.raise_for_status()
        return res.json()


def _report(line: str) -> None:
    # stdout carries the protocol; anything for a person goes to stderr.
    print(line, file=sys.stderr, flush=True)


@functools.cache
def _open_db_once() -> None:
    """Open the database for this process: once, not on every message as it was."""
    db_session.open_db(_report)


def _handle_stdio_line(line: str) -> dict | list | None:
    req = json.loads(line)
    if USE_INPROCESS:
        _open_db_once()
        with Session(db_session.engine) as session:
            return handle_message(session, req, orchestrated=ORCHESTRATED, run_id=RUN_ID)

    if isinstance(req, dict):
        resp = _post_http(req)
        return resp if resp else None
    if isinstance(req, list):
        return _post_http(req)
    return None


def serve_stdio() -> None:
    """Read JSON-RPC messages from stdin until EOF, writing each response to stdout."""
    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = _handle_stdio_line(line)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()


def _run(args: argparse.Namespace) -> str:
    serve_stdio()
    return ""


def register(sub: argparse._SubParsersAction) -> None:
    """Add `mcp serve` to the root CLI's `mcp` group."""
    parser = sub.add_parser(
        "serve",
        help="Speak MCP over stdio, for clients that can only launch a command.",
    )
    parser.set_defaults(run=_run)


def main() -> int:
    serve_stdio()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
