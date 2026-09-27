"""How a supervised run identifies itself to the MCP server.

Two transports, one vocabulary: an HTTP client sends the headers, a stdio
server is launched with the environment variables. Both are written by the
invocation builders in `agents/mcp_context.py` (and the LM Studio loop, which
speaks HTTP itself) and read by `api/mcp.py` and `cli/mcp_server.py`.

The run id only ever travels beside the orchestrated flag. It is a claim, not a
credential: the memory tools check it names a real `agent_runs` row before
recording it as a write's `origin_ref`.
"""

from __future__ import annotations

ORCHESTRATED_HEADER = "X-Loregarden-Orchestrated"
RUN_ID_HEADER = "X-Loregarden-Run"
ORCHESTRATED_ENV = "LOREGARDEN_MCP_ORCHESTRATED"
RUN_ID_ENV = "LOREGARDEN_MCP_RUN_ID"
