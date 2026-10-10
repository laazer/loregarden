"""Local-instance MCP tools: lore-eden's, under loregarden's names.

The four tools — list, launch, status, stop — are written once in
`lore_eden.instances.tools`, over the same manager the Instances panel uses,
so an agent and a person at the UI see and act on the same instances. This
module only names them into loregarden's catalogue and dispatch table.

A verify or visual_qa stage uses them to exercise the change it is reviewing:
launch a branch server from its worktree, launch a client against it, check
what a person would see, stop both. The templates are the only thing that can
run — the tools never take a command — and a branch server boots sandboxed on
a snapshot of main's database (see `services/local_instances.py`).
"""

from __future__ import annotations

from typing import Any

from lore_eden.instances import instance_tools

from loregarden.mcp.tool_ids import LOCAL_INSTANCE_MCP_TOOLS
from loregarden.services.local_instances import get_instance_manager

_PREFIX = "loregarden_"

# Looked up per call, not captured, so a test can substitute the manager.
_PAIRS = instance_tools(lambda: get_instance_manager(), prefix=_PREFIX)

TOOL_DEFINITIONS: list[dict[str, Any]] = [definition.as_payload() for definition, _ in _PAIRS]

#: `EXTENDED_TOOLS` residents. The session is not used: instances live in the
#: registry on disk, not in this database.
HANDLERS: dict[str, Any] = {definition.name: handler for definition, handler in _PAIRS}

# lore-eden decides the tool names; `McpTool` is where loregarden's policy sets
# key on them. Refuse to import if the two disagree, rather than advertise a
# tool no policy covers — it would fall through to the approval inbox with
# nothing saying why.
_unmatched = set(HANDLERS) ^ {tool.value for tool in LOCAL_INSTANCE_MCP_TOOLS}
if _unmatched:
    raise RuntimeError(f"lore-eden instance tools and McpTool disagree on: {sorted(_unmatched)}")
