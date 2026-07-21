"""Whether a tool call needs the operator, across every MCP server.

Two questions were tangled together before, and they are not the same one:

- **Granted** — does this agent have the tool at all? Answered by the agent's
  `mcp_tools` (defaulted by `default_mcp_tools`), which becomes `--tools` on
  the subprocess.
- **Auto-approved** — when it is used, must a human click first? Answered here.

They are orthogonal. A granted tool may still prompt; an auto-approved tool
that was never granted is simply never offered. Collapsing them into one list
would force a choice between "agents cannot use it" and "it runs unsupervised".

This module owns only the second question, and extends it to the servers U1a
made reachable. Until now the check recognised only loregarden's own prefix, so
every call to a registered server stopped for a human — which stalls an
unattended run on its first use.
"""

from __future__ import annotations

import re

from loregarden.models.domain import McpServer
from sqlmodel import Session, select

#: `mcp__<server>__<tool>`, the shape both Claude Code and Cursor use.
_MCP_TOOL_RE = re.compile(r"^mcp__(?P<server>[^_]+(?:_[^_]+)*?)__(?P<tool>.+)$")

LOREGARDEN_SERVER = "loregarden"

#: A server whose tools run without asking. The default is "prompt", because
#: trusting a third party is a decision an operator makes rather than one
#: inherited from having registered it.
POLICY_PROMPT = "prompt"
POLICY_AUTO = "auto"
TOOL_POLICIES = (POLICY_PROMPT, POLICY_AUTO)


def split_mcp_tool(tool_name: str) -> tuple[str, str] | None:
    """`(server, tool)` for an MCP tool name, or None if it is not one."""
    match = _MCP_TOOL_RE.match(tool_name or "")
    if not match:
        return None
    return match.group("server"), match.group("tool")


def server_auto_approves(session: Session, server_name: str) -> bool:
    """Whether a registered server is trusted to run without prompting.

    An unknown or disabled server is not: a tool call naming a server that is
    not registered should be looked at, not waved through.
    """
    server = session.exec(select(McpServer).where(McpServer.name == server_name)).first()
    return bool(server and server.enabled and server.tool_policy == POLICY_AUTO)
