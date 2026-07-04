"""Stdio MCP proxy — optional; prefer POST /mcp on the main server."""

from __future__ import annotations

import json
import os
import sys

import httpx

from loregarden.mcp.protocol import handle_message

API_BASE = os.environ.get("LOREGARDEN_API_BASE", "http://127.0.0.1:8000").rstrip("/")
USE_INPROCESS = os.environ.get("LOREGARDEN_MCP_INPROCESS", "").lower() in ("1", "true", "yes")


def _post_http(body: dict | list) -> dict | list:
    with httpx.Client(base_url=API_BASE, timeout=120.0) as client:
        res = client.post("/mcp", json=body)
        res.raise_for_status()
        return res.json()


def _handle_stdio_line(line: str) -> dict | list | None:
    req = json.loads(line)
    if USE_INPROCESS:
        from loregarden.db.session import engine, init_db
        from sqlmodel import Session

        init_db()
        with Session(engine) as session:
            return handle_message(session, req)

    if isinstance(req, dict):
        resp = _post_http(req)
        return resp if resp else None
    if isinstance(req, list):
        return _post_http(req)
    return None


def main() -> int:
    if len(sys.argv) > 1 and sys.argv[1] == "--cli":
        from loregarden.db.session import engine, init_db
        from sqlmodel import Session

        from loregarden.mcp.tools import execute_tool

        init_db()
        cmd = sys.argv[2]
        raw_args = sys.argv[3:]
        # Minimal CLI: tool name + json args
        args = json.loads(raw_args[0]) if raw_args else {}
        with Session(engine) as session:
            print(execute_tool(session, cmd, args))
        return 0

    for line in sys.stdin:
        line = line.strip()
        if not line:
            continue
        resp = _handle_stdio_line(line)
        if resp is not None:
            sys.stdout.write(json.dumps(resp) + "\n")
            sys.stdout.flush()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
