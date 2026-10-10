"""How a supervised run identifies itself to the MCP server.

Two transports, one vocabulary: an HTTP client sends the headers, a stdio
server is launched with the environment variables. Both are written by the
invocation builders in `agents/mcp_context.py` (and the LM Studio loop, which
speaks HTTP itself) and read by `api/mcp.py` and `cli/mcp_server.py`.

The variables are also set on the agent process itself, so `loregarden mcp
call` run from the agent's own shell (`cli/mcp_tools.py`) carries the run's
identity without the agent having to pass it.

The run id only ever travels beside the orchestrated flag. It is a claim, not a
credential: the memory tools check it names a real `agent_runs` row before
recording it as a write's `origin_ref`.
"""

from __future__ import annotations

from collections.abc import Mapping

ORCHESTRATED_HEADER = "X-Loregarden-Orchestrated"
RUN_ID_HEADER = "X-Loregarden-Run"
ORCHESTRATED_ENV = "LOREGARDEN_MCP_ORCHESTRATED"
RUN_ID_ENV = "LOREGARDEN_MCP_RUN_ID"


def orchestrated_from_env(environ: Mapping[str, str]) -> bool:
    """Whether ``environ`` belongs to a supervised run, read the way stdio does."""
    return environ.get(ORCHESTRATED_ENV, "").lower() in ("1", "true", "yes")


def run_id_from_env(environ: Mapping[str, str]) -> str:
    """The supervised run's id, or "" — it only ever travels beside the flag."""
    return environ.get(RUN_ID_ENV, "") if orchestrated_from_env(environ) else ""
